## DESCRIPTION

*v.osm.import.tag* extracts data from OpenStreetMap by using a specific
OSM tag. This is possible by using the [Overpass
API](https://python-overpy.readthedocs.io/) as input source or by
"incomplete polygon" in the data
[OSMnx](https://osmnx.readthedocs.io/en/stable/). As input area of
interest you can use a vector in GRASS GIS or an external geojson file.
While using an OSM tag satisfy the necessary pattern.
[OSM-GPT](https://osm-gpt.rohitgautam.com.np/) can be used to find the
corresponding OSM tag.

## EXAMPLES

### Download OSM "highways" and store as a GRASS vector map

Download OSM "highways" by their OSM tag('way\["highway"\]') in a
specific area by using an existing vector map (area_of_interest) and
defining an output name (highways).

```sh
v.osm.import.tag aoi_map=area_of_interest osm_tag='way["highway"]' output=highways
```

### Download OSM "highways" in a specific area defined by a geojson file

Download OSM "highways" by their OSM tag('way\["highway"\]') in a
specific area by using a geojson file (area_geojson) and defining an
output name (highways).

```sh
v.osm.import.tag geojson=area_geojson osm_tag='way["highway"]' output=highways
```

The OSM tag has to follow the pattern 'way\["highway"\]' and can contain
various keys and values.

### Download all amenities in a given area

The following example would download all amenities in a given area.

```sh
v.osm.import.tag geojson=area_geojson osm_tag='node["amenity"]' output=amenities
```

### Combination of a key and a value

For specifying your query you can use a combination of a key and a
value. For example use 'way\["amenity"="hospitals"\]' for downloading
all hospitals in your area of interest.

```sh
v.osm.import.tag geojson=area_geojson osm_tag='way["amenity"="hospitals"]' output=hospitals
```

### Combination of multiple OSM tags

For specifying your query you can use multiple OSM tags. For example use
'way\["amenity"="parking"\],way\["parking"="surface"\]' for downloading
all parking areas, which are either tagged with \["amenity"="parking"\]
or \["parking"="surface"\] in your area of interest.

```sh
v.osm.import.tag geojson=area_geojson osm_tag='way["amenity"="parking"],way["parking"="surface"]' output=parking
```

### Specify attributes for attribute table of output vector

In order to only keep attributes of interests in the table of the output
vector, specify keys of OSM Tags in attributes=\[...\] (e.g.
attributes=amenity,parking,access)

```sh
v.osm.import.tag geojson=area_geojson osm_tag='way["amenity"="parking"],way["parking"="surface"]' attributes=amenity,parking,access output=parking
```

**Beware of using the right data type (node, way, relation) provided in
OSM.** Sometimes there is more than one datatype for the same OSM tag.
For example, hospitals are mapped as polygons and points sometimes so
you should use the respective data types to be sure that you get all
mapped features related to your tag. Find your tag at the [OSM
Wiki](https://wiki.openstreetmap.org/wiki/Tags)!

### Importing parking spaces as polygons

The parking spaces can be imported as polygons with the **-p** flag.
**ATTENTION: This works only if the polygon contains only one
LineString!**

```sh
v.osm.import.tag geojson=area_geojson osm_tag='nwr["amenity"="parking"],nwr["parking"="surface"]' output=parking_poly -p
```

## REQUIREMENTS

The following python libraries are used:

- [overpass python library](https://pypi.org/project/overpass/)
- [OSMnx python library](https://osmnx.readthedocs.io/en/stable/)
- [Shapely python library](https://shapely.readthedocs.io/en/stable/manual.html)

If not already done install the overpass package using:

```sh
pip install overpass osmnx shapely
```

On Alpine for OSMnx *proj-dev* is required.

## SEE ALSO

*[v.in.osm](v.in.osm.md),
[v.out.geojson](https://github.com/mundialis/v.out.geojson)*

## AUTHORS

Johannes Halbauer, [mundialis GmbH & Co. KG](https://www.mundialis.de/), Germany

Jonas Pischke, [mundialis GmbH & Co. KG](https://www.mundialis.de/), Germany

Anika Weinmann, [mundialis GmbH & Co. KG](https://www.mundialis.de/), Germany
