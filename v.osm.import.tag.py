#!/usr/bin/env python3
#
############################################################################
#
# MODULE:      v.osm.import.tag
# AUTHOR(S):   Johannes Halbauer
#
# PURPOSE:     Extracts all data selected by a tag from OSM wihtin a defined area
# COPYRIGHT:   (C) 2022 by mundialis GmbH & Co. KG and the GRASS Development Team and the Overpass API Development Team
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

# %rules
# % exclusive: aoi_map, geojson
# %end

import json
import os
import atexit
import shutil
import grass.script as grass

options, flags = grass.parser()

##### define variables

temp_dir = grass.tempdir()

# create string for coordinates
coord_strings = ""

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
    global coord_strings

    coord_strings = []

    for coordinate in coordinates:
        x = coordinate[0]
        y = coordinate[1]
        poly = f"{y} {x}"
        coord_strings.append(poly)

    coord_strings = " ".join(coord_strings)


def cleanup():
    if os.path.isdir(temp_dir):
        shutil.rmtree(temp_dir)


#processing
def main():

    global temp_dir

    if not grass.find_program("v.out.geojson", "--help"):
        grass.fatal(
            _(
                "The 'v.out.geojson' addon module was not found, "
                "install it first:"
            )
            + "\n"
            + "g.extension v.out.geojson url=path/to/addon"
        )

    tmp_geojson = os.path.join(temp_dir, "tmp.geojson")

    # lazy import nonstandard module
    try:
        import overpass
    except ImportError as e:
        grass.fatal(_("Module requires overpass library: {}").format(e))

    # instance api with timeout after 700 seconds
    api = overpass.API(timeout=700)

    area_of_interest = options["geojson"]
    aoi_map = options["aoi_map"]
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

    coords_format(input_geojson["features"][0]["geometry"]["coordinates"][0])

    # rename variable
    my_area = coord_strings

    # define query to get result

    query = []
    for tag in osm_tag:
        query.append(f'{tag}(poly:"{my_area}");')
    query = "".join(query)
    query = f" ({query})"

    # send request to overpass api
    result = api.get(query, verbosity="geom")

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

    output_geojson = os.path.join(temp_dir, f"{output}.geojson")

    # write request result to geojson
    with open(output_geojson, mode="w") as f:
        json.dump(result, f)

    grass.run_command("v.import", input=output_geojson, output=output)

    grass.message(_("Done"))


if __name__ == "__main__":
    options, flags = grass.parser()
    atexit.register(cleanup)
    main()
