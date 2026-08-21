#!/usr/bin/env python3
#
############################################################################
#
# MODULE:      v.osm.import.tag test
# AUTHOR(S):   Jonas Pischke

# PURPOSE:     Tests v.osm.import.tag
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

import os

from grass.gunittest.case import TestCase
from grass.gunittest.main import test
from grass.gunittest.gmodules import SimpleModule
import grass.script as grass


class TestVOSMImportTag(TestCase):
    """
    Main test class
    """

    rm_vec = []
    pid = os.getpid()
    aoi_map_file = "data/test_aoi.gpkg"
    aoi_map = f"aoi_map_{pid}"
    osm_tag = "way['parking'='surface'],way['amenity'='parking']"
    osm_tag_highway = 'way["highway"]'
    attributes = "amenity,parking,surface,access"
    output = f"output_vector_{pid}"
    rm_vec.append((aoi_map))
    GISDBASE = None
    TGTGISRC = None
    TMPLOC = None
    SRCGISRC = None

    @classmethod
    def setUpClass(cls):
        """
        Import the AOI map
        """
        cls.runModule(
            "v.import",
            input=cls.aoi_map_file,
            output=cls.aoi_map,
        )

        grass.run_command("g.region", vector=cls.aoi_map)

    @classmethod
    def tearDownClass(cls):
        """Remove the generated data"""
        for vec in cls.rm_vec:
            cls.runModule(
                "g.remove",
                type="vector",
                name=vec,
                flags="f",
            )

    def tearDown(self):
        """Remove the outputs created
        This is executed after each test run.
        """
        self.runModule(
            "g.remove",
            type="vector",
            name=self.output,
            flags="f",
        )

    def test_default_settings(self):
        """
        Tests module without specified attributes
        """
        check = SimpleModule(
            "v.osm.import.tag",
            osm_tag=self.osm_tag,
            output=self.output,
            aoi_map=self.aoi_map,
            overwrite=True,
        )
        self.assertModule(check)
        self.assertVectorExists(self.output)

        objects = grass.parse_command("v.info", map=self.output, flags="t")
        self.assertTrue(
            (float(objects["lines"]) > 85),
            "Incorrect number of objects. Should be >85",
        )
        self.assertTrue(
            (float(objects["lines"]) < 200),
            "Incorrect number of objects. Should not be > 200",
        )

        cols = grass.parse_command("v.info", map=self.output, flags="c")
        self.assertIn(
            "TEXT|parking" and "TEXT|amenity",
            cols,
            "OSM Tags are not extracted properly.",
        )
        self.assertVectorFitsRegionInfo(
            self.output,
            reference={
                "north": 4484213.65384799,
                "south": 4482438.82567157,
                "east": 6170053.86635061,
                "west": 6168370.0599601,
                "top": 0.0000,
                "bottom": 0.0000,
            },
            msg="Extent of output vector not correct",
            precision=10,
        )

        print("Testing the module successfully finished")

    def test_attribute_selection(self):
        """
        Test module with attribute selection
        """
        check = SimpleModule(
            "v.osm.import.tag",
            osm_tag=self.osm_tag,
            output=self.output,
            attributes="amenity,parking,access",
            aoi_map=self.aoi_map,
            overwrite=True,
        )
        self.assertModule(check)
        self.assertVectorExists(self.output)

        cols = str(
            list(
                grass.parse_command(
                    "v.info", map=self.output, flags="c"
                ).keys()
            )
        )
        ref = str(
            ["INTEGER|cat", "TEXT|access", "TEXT|amenity", "TEXT|parking"]
        )
        self.assertMultiLineEqual(
            cols, ref, msg="Attribute table is not created properly."
        )

    def test_highway_selection(self):
        """
        Test module with ['highway'] tag
        """
        check = SimpleModule(
            "v.osm.import.tag",
            osm_tag=self.osm_tag_highway,
            output=self.output,
            aoi_map=self.aoi_map,
            overwrite=True,
        )
        self.assertModule(check)
        self.assertVectorExists(self.output)

        objects = grass.parse_command("v.info", map=self.output, flags="t")
        self.assertTrue(
            (float(objects["lines"]) > 1700),
            "Incorrect number of objects. Should be > 1700",
        )
        self.assertTrue(
            (float(objects["lines"]) < 2500),
            "Incorrect number of objects. Should be < 2500",
        )

        cols = grass.parse_command("v.info", map=self.output, flags="c")
        self.assertIn(
            "TEXT|highway",
            cols,
            "OSM Tags are not extracted properly.",
        )
        osm_reg = grass.parse_command(
            "g.region", vector=self.output, flags="ug"
        )
        aoi_reg = grass.parse_command(
            "g.region", vector=self.aoi_map, flags="ug"
        )
        self.assertTrue(
            (
                float(osm_reg["n"]) >= float(aoi_reg["n"])
                and float(osm_reg["s"]) <= float(aoi_reg["s"])
                and float(osm_reg["e"]) >= float(aoi_reg["e"])
                and float(osm_reg["w"]) <= float(aoi_reg["w"])
            ),
            msg="Extent of output vector not correct",
        )
        print("Testing the module successfully finished")

    # test for geojson as aoi needs still to be written

    print("Testing the attributes successfully finished")


if __name__ == "__main__":
    test()
