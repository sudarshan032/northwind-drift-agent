"""Approximate city-centre coordinates (public geographic data, 3 decimals)
for the 51 hospital cities, used by the UI map. Cities are real places; the
hospitals are fictional."""

CITY_LATLON = {
    ("Everett", "WA"): (47.979, -122.202), ("Tacoma", "WA"): (47.253, -122.444), ("Seattle", "WA"): (47.606, -122.332),
    ("Spokane", "WA"): (47.659, -117.426), ("Olympia", "WA"): (47.038, -122.900), ("Bellingham", "WA"): (48.750, -122.478),
    ("Yakima", "WA"): (46.602, -120.506), ("Vancouver", "WA"): (45.639, -122.661), ("Kennewick", "WA"): (46.211, -119.137),
    ("Wenatchee", "WA"): (47.424, -120.311), ("Walla Walla", "WA"): (46.065, -118.343), ("Colville", "WA"): (48.547, -117.905),
    ("Ilwaco", "WA"): (46.309, -124.043), ("Forks", "WA"): (47.950, -124.385),
    ("Portland", "OR"): (45.515, -122.679), ("Eugene", "OR"): (44.052, -123.087), ("Medford", "OR"): (42.327, -122.876),
    ("Bend", "OR"): (44.058, -121.315), ("Salem", "OR"): (44.943, -123.035), ("Newberg", "OR"): (45.300, -122.973),
    ("Seaside", "OR"): (45.993, -123.923), ("Hood River", "OR"): (45.705, -121.521), ("Burns", "OR"): (43.586, -119.054),
    ("Los Angeles", "CA"): (34.052, -118.244), ("Burbank", "CA"): (34.181, -118.309), ("Orange", "CA"): (33.788, -117.853),
    ("Santa Monica", "CA"): (34.019, -118.491), ("Mission Hills", "CA"): (34.257, -118.467),
    ("Torrance", "CA"): (33.836, -118.341), ("Fullerton", "CA"): (33.870, -117.925),
    ("Mission Viejo", "CA"): (33.600, -117.672), ("Napa", "CA"): (38.297, -122.286), ("Eureka", "CA"): (40.802, -124.164),
    ("Petaluma", "CA"): (38.232, -122.637), ("Apple Valley", "CA"): (34.501, -117.186),
    ("Lubbock", "TX"): (33.578, -101.855), ("Amarillo", "TX"): (35.222, -101.831), ("Plainview", "TX"): (34.185, -101.707),
    ("Levelland", "TX"): (33.587, -102.378), ("Brownfield", "TX"): (33.181, -102.274),
    ("Missoula", "MT"): (46.872, -113.994), ("Polson", "MT"): (47.694, -114.163), ("Glasgow", "MT"): (48.197, -106.636),
    ("Libby", "MT"): (48.388, -115.556), ("Plentywood", "MT"): (48.775, -104.563),
    ("Seward", "AK"): (60.104, -149.442), ("Kodiak", "AK"): (57.790, -152.407), ("Valdez", "AK"): (61.131, -146.349),
    ("Raton", "NM"): (36.903, -104.439), ("Clayton", "NM"): (36.452, -103.184),
}
