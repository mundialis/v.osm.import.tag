#!/usr/bin/env python3
#
############################################################################
#
# MODULE:      v.osm.import.tag
# AUTHOR(S):   Johannes Halbauer, Jonas Pischke, Anika Weinmann
#
# PURPOSE:     Extracts all data selected by a tag from OSM wihtin a defined
#              area
# COPYRIGHT:   (C) 2022-2025 by mundialis GmbH & Co. KG and the GRASS
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

# %flag
# % key: p
# % description: Convert lines to polygons; Attention: This works only if the polygon contains only one LineString!
# %end

# %rules
# % exclusive: aoi_map, geojson
# %end

import json
import os
import atexit
import grass.script as grass

try:
    from shapely.geometry import Polygon
    import osmnx as ox
    import overpass
except ImportError as e:
    grass.fatal(
        _(f"Module requires shapely, osmnx, and overpass libraries: {e}")
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


def download_data_via_overpass(input_geojson, osm_tag):
    """Download OSM data via overpass api

    Args:
        input_geojson (dict): Geojson dictionary with geometry of aoi
        osm_tag (list): List with OSM tags
    Return:
        output_geojson (str): The output GeoJSON file with the OSM data inside
    """
    # instance api with timeout after 700 seconds
    api = overpass.API(timeout=700)

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

    # send request to overpass api
    try:
        result = api.get(query, verbosity="geom")
    except overpass.errors.UnknownOverpassError as err:
        msg = err.message
        if (
            "incomplete polygon" in msg
            or "inner polygon cannot be matched to outer polygon" in msg
        ):
            return None

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
                result["features"][i]["properties"][
                    attribute.lower()
                ] = feature["properties"][attribute]
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

    # get osm data
    osm_data = ox.features_from_polygon(polygon_geom, tag_dict)

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
            + "g.extension v.out.geojson url=path/to/addon"
        )

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
        with open(f"{area_of_interest}", mode="r") as f:
            input_geojson = json.load(f)

    check_geojson(input_geojson)

    try:
        result_file = download_data_via_overpass(input_geojson, osm_tag)
    except Exception:
        result_file = None
        grass.warning(
            _(
                "Overpass API request failed, "
                "trying to download data via osmnx library..."
            )
        )

    # if overpass OSM import returns None or fails
    # then the import will be tried with osmnx
    if result_file is None:
        result_file = download_data_via_osmnx(input_geojson, osm_tag)

    # import result file
    grass.run_command("v.import", input=result_file, output=output)
    grass.message(_(f"OSM data imported as <{output}>."))


if __name__ == "__main__":
    options, flags = grass.parser()
    atexit.register(cleanup)
    main()
