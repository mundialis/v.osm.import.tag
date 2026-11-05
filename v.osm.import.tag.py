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
# % description: Number of rounds over all Overpass API endpoints before giving up. Each round tries every endpoint once. Default: 4
# %end

# %option
# % key: timeout
# % multiple: no
# % required: no
# % type: integer
# % answer: 300
# % description: Maximum time in seconds to wait for the response of a single Overpass API request before trying the next endpoint. Default: 300
# %end

# %option
# % key: overpass_url
# % multiple: yes
# % required: no
# % type: string
# % description: Custom Overpass API interpreter URL(s) (e.g. a self-hosted instance like http://localhost:12345/api/interpreter), used instead of the global public endpoints. Use the -b flag to keep the public endpoints as fallback
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

# %flag
# % key: e
# % description: Also use Overpass API endpoints which only cover Europe, tried before the global public endpoints. Only set this flag if the AOI is completely inside Europe, otherwise OSM features outside Europe are missing from the result. The data of these endpoints is updated daily.
# %end

# %flag
# % key: b
# % description: Use the global public Overpass API endpoints as fallback if the endpoints given with overpass_url fail
# %end

# %rules
# % exclusive: aoi_map, geojson
# % requires: -b, overpass_url
# %end

import atexit
import contextlib
import json
import os
import re
import time

import grass.script as grass

try:
    import osmnx as ox
    import overpass
    import requests
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
except ImportError as e:
    grass.fatal(
        _(f"Module requires shapely, osmnx, overpass and requests libraries: {e}")
    )

options, flags = grass.parser()

# define variables
temp_dir = grass.tempdir()
rm_files = []
rm_vectors = []

# define output file name
output = options["output"]

# osm element and tag to query
osm_tag = options["osm_tag"]

# split tags if multiple tags
osm_tag = osm_tag.split(",")

# define number of rounds over all endpoints for overpass api query
max_retries = int(options["retries"])

# read timeout (seconds) for a single overpass api request
request_timeout = int(options["timeout"])

# tool to download OSM data
tool = options["tool"].lower()

# Public Overpass API mirrors to try, in this order, before giving up.
# overpass-api.de is the primary instance, the others are community
# mirrors with global coverage which do not need an API key. Other public
# instances are restricted to a region (e.g. overpass.osm.ch) or to
# white-listed usages (e.g. overpass.openstreetmap.fr) and must not be used
# here: an AOI outside of a regional instance can return an empty result
# instead of an error, so the next endpoint would not be tried.
# overpass.kumi.systems is the former name of overpass.private.coffee.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# Public Overpass API instances which only cover Europe (Geofabrik extract,
# daily updates), only used with the -e flag. They return an empty result
# without an error for areas outside of Europe.
OVERPASS_ENDPOINTS_EUROPE = [
    "https://ovp.dresel.systems/api/interpreter",
]

# user defined endpoints (e.g. a self-hosted instance) are tried first and
# replace the global public endpoints, unless the -b flag keeps them as
# fallback
CUSTOM_OVERPASS_ENDPOINTS = (
    options["overpass_url"].split(",") if options["overpass_url"] else []
)
OVERPASS_ENDPOINTS = (
    CUSTOM_OVERPASS_ENDPOINTS
    + (OVERPASS_ENDPOINTS_EUROPE if flags["e"] else [])
    + (OVERPASS_ENDPOINTS if flags["b"] or not CUSTOM_OVERPASS_ENDPOINTS else [])
)

# timeout (seconds) for establishing the connection to an endpoint; dead
# endpoints usually accept the connection and then hang, which is covered
# by request_timeout
OVERPASS_CONNECT_TIMEOUT = 10

# overpass-api.de rejects requests that only carry generic HTTP client
# headers (e.g. the default "python-requests/x.x" User-Agent) with a
# "406 Not Acceptable" error. Always identify ourselves properly and add
# contact info so operators can reach us instead of just blocking us.
OVERPASS_USER_AGENT = (
    "v.osm.import.tag/1.0 (GRASS GIS addon; "
    "https://github.com/mundialis/v.osm.import.tag)"
)

# base delay (seconds) for exponential backoff between rounds
OVERPASS_RETRY_BACKOFF = 2


# define functions
# HTTP status codes that mean "this endpoint rejects us, don't bother
# retrying the same host" (as opposed to transient issues like timeouts,
# connection refused, 429 rate limiting, or 5xx server errors, which are
# worth retrying/backing off on).
# 401 Unauthorized: server requires authentication (we don't have credentials)
# 403 Forbidden: server refuses to serve us (e.g. missing or generic User-Agent)
# 404 Not Found: server doesn't have the requested resource (e.g. endpoint URL
#                changed)
# 406 Not Acceptable: server refuses to serve us (e.g. missing or generic
#                     Accept header)
_PERMANENT_HTTP_ERROR_CODES = (401, 403, 404, 406)

# status code in the error messages of the overpass library ("The request
# returned status code 403") and osmnx ("'host' responded: 403 Forbidden").
# The messages can also contain the query including the AOI coordinates,
# so numbers anywhere else in the message must not be used.
_HTTP_STATUS_PATTERN = re.compile(r"(?:status code|responded:)\s*(\d{3})\b")


def _is_permanent_http_error(exc):
    """Check whether an exception represents a definitive rejection by
    the server (e.g. 403/406) rather than a transient network problem.

    Works both with requests.exceptions.HTTPError (which carries a
    .response object) and with the string-based error messages that
    osmnx/overpass sometimes raise instead (e.g. "'host' responded:
    403 Forbidden").
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None) if response else None
    if status_code is None:
        match = _HTTP_STATUS_PATTERN.search(str(exc))
        if match:
            status_code = int(match.group(1))
    return status_code in _PERMANENT_HTTP_ERROR_CODES


def get_aoi_polygons(input_json):
    """Get the polygons of all features of the AOI geojson

    Args:
        input_json (dict): Geojson dictionary with geometry of aoi

    Return:
        polygons (list): List of polygons, each a list of rings (outer ring
                         first, then holes) with [lon, lat] coordinates
    """
    polygons = []
    for feature in input_json["features"]:
        geometry = feature.get("geometry") or {}
        if geometry.get("type") == "Polygon":
            polygons.append(geometry["coordinates"])
        elif geometry.get("type") == "MultiPolygon":
            polygons.extend(geometry["coordinates"])
        else:
            grass.warning(
                _(
                    f"Ignoring AOI feature with geometry type "
                    f"{geometry.get('type')}, only polygons are supported."
                )
            )
    return polygons


def check_geojson(input_json):
    """Check if there are polygons contained in geojson

    Return:
        polygons (list): The AOI polygons, see get_aoi_polygons
    """
    if len(input_json["features"]) == 0:
        grass.message(_("Stopped, because of no feature contained in geojson."))
        quit()

    polygons = get_aoi_polygons(input_json)
    if not polygons:
        grass.fatal(_("The AOI does not contain any polygon."))

    grass.message(
        _(
            f"Using {len(polygons)} polygon(s) of "
            f"{len(input_json['features'])} feature(s) as AOI."
        )
    )
    grass.message(_("Starting to process..."))
    return polygons


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
    for rm_vector in rm_vectors:
        if grass.find_file(rm_vector, element="vector", mapset=".")["file"]:
            grass.run_command(
                "g.remove", type="vector", name=rm_vector, flags="f", quiet=True
            )
    if os.path.isdir(temp_dir):
        grass.try_rmdir(temp_dir)


def convert_lines_to_polygons(result):
    """Convert line features in polygons"""
    for el in result["features"]:
        if el["geometry"]["type"] == "LineString":
            el["geometry"]["coordinates"].append(el["geometry"]["coordinates"][0])
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


def _request_with_endpoint_fallback(request, label, no_retry_exceptions=()):
    """Run a request against all Overpass endpoints in rounds.

    Each round tries every endpoint once, so a hanging or overloaded
    endpoint does not block the others. Between rounds there is an
    exponential backoff. Endpoints that definitively reject us (e.g.
    403/406) are dropped for the remaining rounds.

    Args:
        request (callable): function taking the endpoint URL and returning
                            the result of the request
        label (str): name of the request tool used in messages
        no_retry_exceptions (tuple): exception types which are raised
                                     immediately, e.g. for an invalid query

    Returns:
        the result of the first successful request

    Raises:
        The last encountered exception if every endpoint failed in every
        round.
    """
    endpoints = list(OVERPASS_ENDPOINTS)
    last_exc = None
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(OVERPASS_RETRY_BACKOFF**attempt)
        for endpoint in list(endpoints):
            try:
                return request(endpoint)
            except no_retry_exceptions:
                raise
            except Exception as e:
                last_exc = e
                if _is_permanent_http_error(e):
                    grass.warning(
                        _(
                            f"{label} endpoint {endpoint} rejected the "
                            f"request ({e}), not using it anymore."
                        )
                    )
                    endpoints.remove(endpoint)
                    continue
                grass.warning(
                    _(
                        f"{label} request to {endpoint} failed "
                        f"(round {attempt + 1}/{max_retries}): {e}"
                    )
                )
        if not endpoints:
            break
    # every endpoint failed
    raise last_exc


def _query_overpass_with_retries(query, timeout):
    """Query several Overpass endpoints with retries/backoff.

    Args:
        query (str): full Overpass QL query (without leading endpoint)
        timeout (int): per-request read timeout in seconds

    Returns:
        dict: the parsed (GeoJSON-like) overpass response

    Raises:
        The last encountered exception if every endpoint/attempt failed
        with a connection-type error (caller can then fall back to
        osmnx).
    """

    def request(endpoint):
        api = overpass.API(
            endpoint=endpoint,
            timeout=(OVERPASS_CONNECT_TIMEOUT, timeout),
            user_agent=OVERPASS_USER_AGENT,
        )
        return api.get(query, verbosity="geom")

    with _polite_overpass_headers():
        # an invalid query (e.g. wrong tag syntax or broken polygon) will
        # fail on every endpoint, so retrying / switching endpoint will not
        # help
        return _request_with_endpoint_fallback(
            request,
            "Overpass",
            (
                overpass.errors.OverpassSyntaxError,
                overpass.errors.UnknownOverpassError,
            ),
        )


def download_data_via_overpass(polygons, osm_tag):
    """Download OSM data via overpass api

    Args:
        polygons (list): AOI polygons, see get_aoi_polygons
        osm_tag (list): List with OSM tags

    Return:
        output_geojson (str): The output GeoJSON file with the OSM data inside
    """
    # define query to get result: one poly filter per AOI polygon (outer
    # ring) and tag, the result is the union of all of them
    query = []
    for polygon in polygons:
        coord_strings = coords_format(polygon[0])
        for tag in osm_tag:
            query.append(f'{tag}(poly:"{coord_strings}");')
    query = "".join(query)
    query = f" ({query})"

    # send request to overpass api (with mirror fallback + retries)
    try:
        result = _query_overpass_with_retries(query, timeout=request_timeout)
    except overpass.errors.OverpassSyntaxError:
        grass.fatal(
            _(
                f"Overpass rejected the query as invalid. Check the syntax of "
                f"osm_tag <{','.join(osm_tag)}>, it has to be an Overpass QL "
                f'filter like \'way["highway"]\' or \'nwr["leisure"="park"]\'.'
            )
        )
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
            if attribute.lower() in attributes and attribute != attribute.lower():
                new_name = f"{attribute.lower()}"
                if attribute.lower() in attribute_names_lower:
                    while new_name in attribute_names_lower:
                        new_name += "2"
                result["features"][i]["properties"][new_name] = feature["properties"][
                    attribute
                ]
                del result["features"][i]["properties"][attribute]
                attribute_names_lower.append(new_name)
            elif attribute != attribute.lower():
                result["features"][i]["properties"][attribute.lower()] = feature[
                    "properties"
                ][attribute]
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


def download_data_via_osmnx(polygons, osm_tag):
    """Download OSM data via osmnx lib

    Args:
        polygons (list): AOI polygons, see get_aoi_polygons
        osm_tag (list): List with OSM tags

    Return:
        output_file (str): The output GPKG file with the OSM data inside
    """

    # create shapely (multi)polygon including holes from the AOI polygons
    polygon_geom = unary_union([Polygon(rings[0], rings[1:]) for rings in polygons])

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
    ox.settings.requests_timeout = request_timeout

    def request(endpoint):
        # osmnx expects the base URL of the API and appends "/interpreter"
        ox.settings.overpass_url = endpoint.rstrip("/").removesuffix("/interpreter")
        # osmnx waits for a free slot according to the /status page, which
        # never reports one on servers without rate limit (e.g. self-hosted
        # ones), so only do this for the public endpoints
        ox.settings.overpass_rate_limit = endpoint not in CUSTOM_OVERPASS_ENDPOINTS
        return ox.features_from_polygon(polygon_geom, tag_dict)

    # get osm data, with mirror fallback + retries for connection issues
    try:
        osm_data = _request_with_endpoint_fallback(
            request, "osmnx", (ox._errors.InsufficientResponseError,)
        )
    except ox._errors.InsufficientResponseError as e:
        if flags["f"]:
            grass.warning(_(f"No OSM features found for query {tag_dict}: {e}"))
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
        grass.fatal(_(f"OSMnx query failed with unexpected error: {e}"))

    # get columns and filter columns
    column_names = list(osm_data.columns.values)
    for col in column_names:
        # Join the list into a string
        if any(isinstance(val, list) for val in osm_data[col]):
            osm_data[col] = osm_data[col].apply(lambda x: str(x))
    osm_data2 = osm_data.loc[:, osm_data.columns.str.contains("|".join(column_names))]

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
            _("The 'v.out.geojson' addon module was not found, install it first:")
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

    polygons = check_geojson(input_geojson)

    if flags["e"]:
        grass.warning(
            _(
                "Flag -e is set: the AOI must be completely inside Europe, "
                "otherwise OSM features outside Europe are missing from the "
                "result. The data of the Europe endpoints is updated daily."
            )
        )

    if tool == "overpass":
        try:
            result_file = download_data_via_overpass(polygons, osm_tag)
        except Exception as e:
            result_file = None
            msg = _(
                f"Overpass API request failed: {e} You can try "
                "to download data via osmnx library using tool=osmnx..."
            )
            if flags["f"]:
                grass.warning(msg)
            else:
                grass.fatal(msg)
    elif tool == "osmnx":
        result_file = download_data_via_osmnx(polygons, osm_tag)

    # import result file
    if result_file:
        if any(len(rings) > 1 for rings in polygons):
            # the overpass query only uses the outer rings of the AOI
            # polygons, so remove features which are completely inside
            # holes of the AOI
            grass.message(_("Removing features inside holes of the AOI..."))
            tmp_name = f"tmp_v_osm_import_tag_{os.getpid()}"
            if aoi_map:
                aoi_vector = aoi_map
            else:
                aoi_vector = f"{tmp_name}_aoi"
                rm_vectors.append(aoi_vector)
                grass.run_command(
                    "v.import", input=area_of_interest, output=aoi_vector, quiet=True
                )
            rm_vectors.append(tmp_name)
            grass.run_command(
                "v.import", input=result_file, output=tmp_name, quiet=True
            )
            grass.run_command(
                "v.select",
                ainput=tmp_name,
                binput=aoi_vector,
                operator="overlap",
                output=output,
                quiet=True,
            )
        else:
            grass.run_command("v.import", input=result_file, output=output)
        grass.message(_(f"OSM data imported as <{output}>."))


if __name__ == "__main__":
    options, flags = grass.parser()
    atexit.register(cleanup)
    main()
