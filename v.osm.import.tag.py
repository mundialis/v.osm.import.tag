#!/usr/bin/env python3
#
############################################################################
#
# MODULE:      v.osm.import.tag
# AUTHOR(S):   Johannes Halbauer, Jonas Pischke, Anika Weinmann
#
# PURPOSE:     Extracts all data selected by a tag from OSM wihtin a defined
#              area
# COPYRIGHT:   (C) 2022-2026 by mundialis GmbH & Co. KG and the GRASS
#              Development Team and the Overpass API Development Team
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
#############################################################################

# %Module
# % description: Extracts all data selected by an OSM tag (through the Overpass API as inpuit source) within a specified area (geojson or vector in GRASS as input) and outputs a geojson and a vector file in GRASS.
# % keyword: vector
# % keyword: roads
# % keyword: OSM
# %end

# %option G_OPT_V_INPUT
# % key: aoi_map
# % required: no
# % description: Existing vector AOI map in GRASS as input
# %end

# %option G_OPT_F_INPUT
# % key: geojson
# % required: no
# % description: Name of AOI geojson file as input
# %end

# %option
# % key: osm_tag
# % multiple: yes
# % required: yes
# % type: string
# % description: OSM tag for querying a specific part of OSM data(form: 'way["highway"]')
# %end

# %option
# % key: attributes
# % multiple: yes
# % required: no
# % type: string
# % description: Keys from OSM tags which shall be extracted for attribute table of output vector (e.g. surface,access,...)
# %end

# %option G_OPT_V_OUTPUT
# % key: output
# % required: yes
# % multiple: no
# % description: Output name of processing result vector map
# %end

# %option
# % key: retries
# % multiple: no
# % required: no
# % type: integer
# % answer: 4
# % description: Number of times to retry the Overpass API query. Default: 4
# %end

# %option
# % key: tool
# % multiple: no
# % required: no
# % type: string
# % answer: overpass
# % description: Tool to download OSM data. Options: overpass, osmnx. Default: overpass
# %end

# %flag
# % key: p
# % description: Convert lines to polygons; Attention: This works only if the polygon contains only one LineString!
# %end

# %flag
# % key: f
# % description: Allow failing query or rather no matching feature found. If this flag is not set, the module will fail if no matching feature is found for the query. If the flag is set, the module will continue without output generation (Reasonable if sure that query is correct, but aoi_map might not contatin requested query).
# %end

# %rules
# % exclusive: aoi_map, geojson
# %end

import contextlib
import json
import os
import atexit
import time

import grass.script as grass

try:
    from shapely.geometry import Polygon
    import osmnx as ox
    import overpass
    import requests
except ImportError as e:
    grass.fatal(
        _(
            f"Module requires shapely, osmnx, overpass and requests libraries: {e}"
        )
    )

options, flags = grass.parser()

##### define variables
temp_dir = grass.tempdir()
rm_files = []

# define output file name
output = options["output"]

# osm element and tag to query
osm_tag = options["osm_tag"]

# split tags if multiple tags
osm_tag = osm_tag.split(",")

# define number of retries for overpass api query
max_retries = int(options["retries"])

# tool to download OSM data
tool = options["tool"].lower()

# Public Overpass API mirrors to try, in this order, before giving up.
# overpass-api.de is the primary instance, the others are community
# mirrors.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

# overpass-api.de rejects requests that only carry generic HTTP client
# headers (e.g. the default "python-requests/x.x" User-Agent) with a
# "406 Not Acceptable" error. Always identify ourselves properly and add
# contact info so operators can reach us instead of just blocking us.
OVERPASS_USER_AGENT = (
    "v.osm.import.tag/1.0 (GRASS GIS addon; "
    "https://github.com/mundialis/v.osm.import.tag)"
)

# base delay (seconds) for exponential backoff between retries
OVERPASS_RETRY_BACKOFF = 2


#### define functions
def check_geojson(input_json):
    """Check if there is a feature contained in geojson"""
    if len(input_json["features"]) == 0:
        grass.message(
            _("Stopped, because of no feature contained in geojson.")
        )
        quit()

    elif len(input_json["features"]) > 1:
        grass.message(_("There are more than one feature in the geojson."))
        grass.message(_("Only the first feature will be used!"))
        grass.message(_("Starting to process..."))

    else:
        grass.message(_("Starting to process..."))


def coords_format(coordinates):
    """
    change structure of coordinates to "x y x y ..."
    needed for the overpass query
    """

    coord_strings = []

    for coordinate in coordinates:
        x = coordinate[0]
        y = coordinate[1]
        poly = f"{y} {x}"
        coord_strings.append(poly)

    coord_strings = " ".join(coord_strings)
    return coord_strings


def cleanup():
    """
    general cleanup function
    """
    for rm_file in rm_files:
        if os.path.isfile(rm_file):
            try:
                os.remove(rm_file)
            except Exception:
                grass.warning(f"{rm_file} could not be deleted!")
    if os.path.isdir(temp_dir):
        grass.try_rmdir(temp_dir)


def convert_lines_to_polygons(result):
    """Convert line features in polygons"""
    for el in result["features"]:
        if el["geometry"]["type"] == "LineString":
            el["geometry"]["coordinates"].append(
                el["geometry"]["coordinates"][0]
            )
            el["geometry"]["coordinates"] = [el["geometry"]["coordinates"]]
            el["geometry"]["type"] = "Polygon"
        else:
            continue
    grass.message(_("Lines are converted to polygons."))


@contextlib.contextmanager
def _polite_overpass_headers():
    """Temporarily make every outgoing `requests` call send a proper
    User-Agent and Accept header.

    The `overpass` python package (mvexel/overpass-api-python-wrapper) is
    unmaintained/archived and does not expose a way to set custom HTTP
    headers. overpass-api.de now rejects "impolite" requests (missing or
    generic User-Agent) with HTTP 406. This context manager monkeypatches
    requests.Session.request just for the duration of the overpass call,
    so the rest of the module (e.g. osmnx) is unaffected.
    """
    original_request = requests.Session.request

    def patched_request(self, method, url, *args, **kwargs):
        headers = kwargs.pop("headers", None) or {}
        headers.setdefault("User-Agent", OVERPASS_USER_AGENT)
        headers.setdefault("Accept", "application/json")
        headers.setdefault("Accept-Charset", "utf-8;q=0.7,*;q=0.7")
        headers.setdefault("Accept-Encoding", "gzip, deflate")
        kwargs["headers"] = headers
        return original_request(self, method, url, *args, **kwargs)

    requests.Session.request = patched_request
    try:
        yield
    finally:
        requests.Session.request = original_request


def _query_overpass_with_retries(query, timeout):
    """Query several Overpass endpoints with retries/backoff.

    Args:
        query (str): full Overpass QL query (without leading endpoint)
        timeout (int): per-request timeout in seconds

    Returns:
        dict: the parsed (GeoJSON-like) overpass response

    Raises:
        The last encountered exception if every endpoint/attempt failed
        with a connection-type error (caller can then fall back to
        osmnx).
    """
    last_exc = None
    with _polite_overpass_headers():
        for endpoint in OVERPASS_ENDPOINTS:
            api = overpass.API(endpoint=endpoint, timeout=timeout)
            for attempt in range(max_retries):
                try:
                    return api.get(query, verbosity="geom")
                except overpass.errors.UnknownOverpassError:
                    # query itself is invalid (e.g. broken polygon) -
                    # retrying / switching endpoint will not help
                    raise
                except Exception as e:
                    last_exc = e
                    if _is_permanent_http_error(e):
                        grass.warning(
                            _(
                                f"Overpass endpoint {endpoint} rejected the "
                                f"request ({e}), skipping to next endpoint."
                            )
                        )
                        break
                    grass.warning(
                        _(
                            f"Overpass request to {endpoint} failed "
                            f"(attempt {attempt + 1}/{max_retries}): {e}"
                        )
                    )
                    time.sleep(OVERPASS_RETRY_BACKOFF**attempt)
            else:
                # loop completed without a `break` -> all retries used up
                grass.warning(
                    _(
                        f"Giving up on {endpoint} after "
                        f"{max_retries} attempts."
                    )
                )
    # every endpoint failed
    raise last_exc


def download_data_via_overpass(input_geojson, osm_tag):
    """Download OSM data via overpass api

    Args:
        input_geojson (dict): Geojson dictionary with geometry of aoi
        osm_tag (list): List with OSM tags

    Return:
        output_geojson (str): The output GeoJSON file with the OSM data inside
    """
    # get coordinates from input geojson
    coord_strings = coords_format(
        input_geojson["features"][0]["geometry"]["coordinates"][0]
    )

    # define query to get result
    query = []
    for tag in osm_tag:
        query.append(f'{tag}(poly:"{coord_strings}");')
    query = "".join(query)
    query = f" ({query})"

    # send request to overpass api (with mirror fallback + retries)
    try:
        result = _query_overpass_with_retries(query, timeout=700)
    except overpass.errors.UnknownOverpassError as err:
        msg = err.message
        if (
            "incomplete polygon" in msg
            or "inner polygon cannot be matched to outer polygon" in msg
        ):
            return None
        raise

    attributes = list()
    for i, feature in enumerate(result["features"]):
        if not isinstance(feature, dict):
            continue
        attribute_names_lower = [
            attr.lower() if isinstance(attr, str) else str(attr)
            for attr in feature["properties"]
        ]
        attribute_names = [
            attr for attr in feature["properties"] if isinstance(attr, str)
        ]

        for attribute in attribute_names:
            if (
                attribute.lower() in attributes
                and attribute != attribute.lower()
            ):
                new_name = f"{attribute.lower()}"
                if attribute.lower() in attribute_names_lower:
                    while new_name in attribute_names_lower:
                        new_name += "2"
                result["features"][i]["properties"][new_name] = feature[
                    "properties"
                ][attribute]
                del result["features"][i]["properties"][attribute]
                attribute_names_lower.append(new_name)
            elif attribute != attribute.lower():
                result["features"][i]["properties"][attribute.lower()] = (
                    feature["properties"][attribute]
                )
                del result["features"][i]["properties"][attribute]

        attributes.extend(attribute_names_lower)

    # subset of specified keys for attribute table
    col = options["attributes"]
    if col:
        col = col.split(",")
        con = []
        for k in col:
            con.append(f"key != '{k}'")
        con = " and ".join(con)
        for property in result["features"]:
            for key in list(property["properties"].keys()):
                if eval(con):
                    del property["properties"][key]
                else:
                    continue
    else:
        grass.message(_("No attributes set"))

    # convert lines to polygons
    if flags["p"]:
        convert_lines_to_polygons(result)

    output_geojson = os.path.join(temp_dir, f"{output}.geojson")

    # write request result to geojson
    with open(output_geojson, mode="w") as f:
        json.dump(result, f)
    return output_geojson


def download_data_via_osmnx(input_geojson, osm_tag):
    """Download OSM data via osmnx lib

    Args:
        input_geojson (dict): Geojson dictionary with geometry of aoi
        osm_tag (list): List with OSM tags

    Return:
        output_file (str): The output GPKG file with the OSM data inside
    """

    # create shapely polygon from input_geojson
    coordinates = input_geojson["features"][0]["geometry"]["coordinates"][0]
    lon_point_list = [x[0] for x in coordinates]
    lat_point_list = [x[1] for x in coordinates]
    polygon_geom = Polygon(zip(lon_point_list, lat_point_list))

    # define tag dict
    tag_dict = {}
    type_list = []
    for tag in osm_tag:
        g_type, rest_tag = tag.split("]")[0].split("[")
        if "=" in rest_tag:
            key, val = rest_tag.split("=")
            tag_dict[key.strip('"').strip("'")] = val.strip('"').strip("'")
        else:
            tag_dict[rest_tag.strip('"').strip("'")] = True
        type_list.append(g_type)

    # identify politely and only rely on osmnx's own (already well
    # behaved) request headers/rate limiting
    ox.settings.useragent = OVERPASS_USER_AGENT
    ox.settings.referer = OVERPASS_USER_AGENT

    # get osm data, with mirror fallback + retries for connection issues
    osm_data = None
    last_exc = None
    for endpoint in OVERPASS_ENDPOINTS:
        ox.settings.overpass_url = endpoint
        for attempt in range(max_retries):
            try:
                osm_data = ox.features_from_polygon(polygon_geom, tag_dict)
                last_exc = None
                break
            except ox._errors.InsufficientResponseError as e:
                if flags["f"]:
                    grass.warning(
                        _(f"No OSM features found for query {tag_dict}: {e}")
                    )
                    return
                else:
                    grass.fatal(
                        _(
                            f"No OSM features found for query {tag_dict}: {e}. "
                            "If query correct, but not contained within AOI "
                            "consider using the -f flag to allow failing query."
                        )
                    )
            except Exception as e:
                last_exc = e
                if _is_permanent_http_error(e):
                    grass.warning(
                        _(
                            f"osmnx endpoint {endpoint} rejected the "
                            f"request ({e}), skipping to next endpoint."
                        )
                    )
                    break
                grass.warning(
                    _(
                        f"osmnx request to {endpoint} failed "
                        f"(attempt {attempt + 1}/{max_retries}): {e}"
                    )
                )
                time.sleep(OVERPASS_RETRY_BACKOFF**attempt)
        if last_exc is None:
            break
        if not _is_permanent_http_error(last_exc):
            grass.warning(
                _(f"Giving up on {endpoint} after {max_retries} attempts.")
            )

    if last_exc is not None:
        grass.fatal(_(f"OSMnx query failed with unexpected error: {last_exc}"))

    # get columns and filter columns
    column_names = list(osm_data.columns.values)
    for col in column_names:
        # Join the list into a string
        if any(isinstance(val, list) for val in osm_data[col]):
            osm_data[col] = osm_data[col].apply(lambda x: str(x))
    osm_data2 = osm_data.loc[
        :, osm_data.columns.str.contains("|".join(column_names))
    ]

    # # filter geometry
    # osm_data3 = osm_data2.loc[osm_data2.geometry.type == "Polygon"]

    # save osm data to GeoJson
    output_file = os.path.join(temp_dir, f"{output}.geojson")
    rm_files.append(output_file)
    osm_data2.to_file(output_file, driver="GeoJSON")

    # convert lines to polygons
    if flags["p"]:
        with open(output_file) as f_in:
            data = json.load(f_in)
        convert_lines_to_polygons(data)
        output_file = os.path.join(temp_dir, f"{output}_poly.geojson")
        rm_files.append(output_file)
        with open(output_file, "w") as f_out:
            json.dump(data, f_out)

    return output_file


def main():
    """Main function of v.osm.import.tag"""
    global temp_dir, rm_files

    area_of_interest = options["geojson"]
    aoi_map = options["aoi_map"]

    if not grass.find_program("v.out.geojson", "--help"):
        grass.fatal(
            _(
                "The 'v.out.geojson' addon module was not found, "
                "install it first:"
            )
            + "\n"
            + "g.extension v.out.geojson url=https://github.com/mundialis/v.out.geojson"
        )

    if aoi_map:
        if not grass.find_file(aoi_map, "vector")["file"]:
            grass.fatal(_("Vector map <%s> not found") % aoi_map)

    # get aoi as geojson
    tmp_geojson = os.path.join(temp_dir, "tmp.geojson")
    if aoi_map:
        grass.run_command("v.out.geojson", input=aoi_map, output=tmp_geojson)

    # load AOI (geojson) as dict
    input_geojson = None
    if aoi_map:
        with open(tmp_geojson, mode="r") as f:
            input_geojson = json.load(f)
    else:
        if not os.access(area_of_interest, os.R_OK):
            grass.fatal(_("File <%s> not found") % area_of_interest)
        with open(f"{area_of_interest}", mode="r") as f:
            input_geojson = json.load(f)

    check_geojson(input_geojson)

    if tool == "overpass":
        try:
            result_file = download_data_via_overpass(input_geojson, osm_tag)
        except Exception as e:
            result_file = None
            grass.error(
                _(
                    "Overpass API request failed, "
                    "trying to download data via osmnx library..."
                )
            )
    elif tool == "osmnx":
        result_file = download_data_via_osmnx(input_geojson, osm_tag)

    # import result file
    if result_file:
        grass.run_command("v.import", input=result_file, output=output)
        grass.message(_(f"OSM data imported as <{output}>."))


if __name__ == "__main__":
    options, flags = grass.parser()
    atexit.register(cleanup)
    main()
