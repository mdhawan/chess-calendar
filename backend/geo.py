"""Offline geocoding + state normalization for Indian tournaments.

Single source of truth for Indian geography. No network calls — everything is
resolved from bundled tables so refreshes stay fast and self-contained.

Public API
----------
- ``canonical_state(state, city)`` -> canonical state/UT name, or None if the
  input can't be resolved to an Indian state.
- ``geocode(city, state)`` -> ``(lat, lng, precision)`` where precision is
  ``"city"`` (matched a gazetteer city), ``"state"`` (fell back to the state
  centroid), or ``None`` (unresolvable).

The city gazetteer covers the ~150 largest / most tournament-active Indian
cities; anything not in it falls back to the state centroid, so every row with
a recognisable Indian state still gets a pin.
"""
from __future__ import annotations

import re
from typing import Optional

# The 28 states + 8 union territories, canonical spellings.
CANONICAL_STATES: list[str] = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka",
    "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya",
    "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim",
    "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal",
    # Union territories
    "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]

_CANONICAL_LOWER = {s.lower(): s for s in CANONICAL_STATES}

# Common abbreviations / alternate spellings seen across sources. Keys are
# compared case-insensitively.
STATE_ALIASES: dict[str, str] = {
    " up": "Uttar Pradesh", "u.p.": "Uttar Pradesh", "uttarpradesh": "Uttar Pradesh",
    "mp": "Madhya Pradesh", "m.p.": "Madhya Pradesh", "madhyapradesh": "Madhya Pradesh",
    "tn": "Tamil Nadu", "tamilnadu": "Tamil Nadu",
    "ap": "Andhra Pradesh", "a.p.": "Andhra Pradesh", "andhrapradesh": "Andhra Pradesh",
    "wb": "West Bengal", "w.b.": "West Bengal", "westbengal": "West Bengal",
    "hp": "Himachal Pradesh", "h.p.": "Himachal Pradesh", "himachalpradesh": "Himachal Pradesh",
    "j&k": "Jammu and Kashmir", "jk": "Jammu and Kashmir",
    "jammu & kashmir": "Jammu and Kashmir", "jammu and kashmir": "Jammu and Kashmir",
    "cg": "Chhattisgarh", "c.g.": "Chhattisgarh", "chattisgarh": "Chhattisgarh",
    "odisha": "Odisha", "orissa": "Odisha",
    "pondicherry": "Puducherry", "pondy": "Puducherry",
    "ncr": "Delhi", "new delhi": "Delhi", "delhi ncr": "Delhi", "n.c.t. of delhi": "Delhi",
    "uttaranchal": "Uttarakhand",
    "andaman and nicobar": "Andaman and Nicobar Islands",
    "andaman & nicobar islands": "Andaman and Nicobar Islands",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "karntaka": "Karnataka",  # frequent typo
}

# One representative lat/lng per state/UT (approx. capital or geographic centre).
STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "Andhra Pradesh": (15.9129, 79.7400),
    "Arunachal Pradesh": (28.2180, 94.7278),
    "Assam": (26.2006, 92.9376),
    "Bihar": (25.0961, 85.3131),
    "Chhattisgarh": (21.2787, 81.8661),
    "Goa": (15.2993, 74.1240),
    "Gujarat": (22.2587, 71.1924),
    "Haryana": (29.0588, 76.0856),
    "Himachal Pradesh": (31.1048, 77.1734),
    "Jharkhand": (23.6102, 85.2799),
    "Karnataka": (15.3173, 75.7139),
    "Kerala": (10.8505, 76.2711),
    "Madhya Pradesh": (23.4733, 77.9470),
    "Maharashtra": (19.7515, 75.7139),
    "Manipur": (24.6637, 93.9063),
    "Meghalaya": (25.4670, 91.3662),
    "Mizoram": (23.1645, 92.9376),
    "Nagaland": (26.1584, 94.5624),
    "Odisha": (20.9517, 85.0985),
    "Punjab": (31.1471, 75.3412),
    "Rajasthan": (27.0238, 74.2179),
    "Sikkim": (27.5330, 88.5122),
    "Tamil Nadu": (11.1271, 78.6569),
    "Telangana": (17.1232, 79.2088),
    "Tripura": (23.9408, 91.9882),
    "Uttar Pradesh": (26.8467, 80.9462),
    "Uttarakhand": (30.0668, 79.0193),
    "West Bengal": (22.9868, 87.8550),
    "Andaman and Nicobar Islands": (11.7401, 92.6586),
    "Chandigarh": (30.7333, 76.7794),
    "Dadra and Nagar Haveli and Daman and Diu": (20.3974, 72.8328),
    "Delhi": (28.7041, 77.1025),
    "Jammu and Kashmir": (33.7782, 76.5762),
    "Ladakh": (34.1526, 77.5770),
    "Lakshadweep": (10.5667, 72.6417),
    "Puducherry": (11.9416, 79.8083),
}

# Major / tournament-active Indian cities -> (lat, lng, canonical state).
# Keys are lowercase; lookups normalise the incoming city string first.
CITY_GAZETTEER: dict[str, tuple[float, float, str]] = {
    # Andhra Pradesh
    "visakhapatnam": (17.6868, 83.2185, "Andhra Pradesh"),
    "vizag": (17.6868, 83.2185, "Andhra Pradesh"),
    "vijayawada": (16.5062, 80.6480, "Andhra Pradesh"),
    "guntur": (16.3067, 80.4365, "Andhra Pradesh"),
    "nellore": (14.4426, 79.9865, "Andhra Pradesh"),
    "tirupati": (13.6288, 79.4192, "Andhra Pradesh"),
    "rajahmundry": (17.0005, 81.8040, "Andhra Pradesh"),
    "kakinada": (16.9891, 82.2475, "Andhra Pradesh"),
    "kurnool": (15.8281, 78.0373, "Andhra Pradesh"),
    "vizianagaram": (18.1067, 83.3956, "Andhra Pradesh"),
    "eluru": (16.7107, 81.0952, "Andhra Pradesh"),
    "anantapur": (14.6819, 77.6006, "Andhra Pradesh"),
    "kadapa": (14.4674, 78.8241, "Andhra Pradesh"),
    "ongole": (15.5057, 80.0499, "Andhra Pradesh"),
    # Arunachal Pradesh
    "itanagar": (27.0844, 93.6053, "Arunachal Pradesh"),
    # Assam
    "guwahati": (26.1445, 91.7362, "Assam"),
    "dibrugarh": (27.4728, 94.9120, "Assam"),
    "silchar": (24.8333, 92.7789, "Assam"),
    "jorhat": (26.7509, 94.2037, "Assam"),
    "nagaon": (26.3452, 92.6840, "Assam"),
    "tezpur": (26.6338, 92.7926, "Assam"),
    "duliajan": (27.3667, 95.3333, "Assam"),
    "nalbari": (26.4445, 91.4386, "Assam"),
    "sivasagar": (26.9853, 94.6377, "Assam"),
    # Bihar
    "patna": (25.5941, 85.1376, "Bihar"),
    "gaya": (24.7955, 85.0002, "Bihar"),
    "bhagalpur": (25.2425, 86.9842, "Bihar"),
    "muzaffarpur": (26.1209, 85.3647, "Bihar"),
    "darbhanga": (26.1542, 85.8918, "Bihar"),
    "chapra": (25.7811, 84.7475, "Bihar"),
    "katihar": (25.5391, 87.5822, "Bihar"),
    # Chhattisgarh
    "raipur": (21.2514, 81.6296, "Chhattisgarh"),
    "bhilai": (21.1938, 81.3509, "Chhattisgarh"),
    "durg": (21.1904, 81.2849, "Chhattisgarh"),
    "bilaspur": (22.0797, 82.1409, "Chhattisgarh"),
    "ambikapur": (23.1204, 83.1958, "Chhattisgarh"),
    "korba": (22.3595, 82.7501, "Chhattisgarh"),
    # Goa
    "panaji": (15.4909, 73.8278, "Goa"),
    "panjim": (15.4909, 73.8278, "Goa"),
    "margao": (15.2832, 73.9862, "Goa"),
    "vasco da gama": (15.3981, 73.8113, "Goa"),
    "mapusa": (15.5937, 73.8143, "Goa"),
    # Gujarat
    "ahmedabad": (23.0225, 72.5714, "Gujarat"),
    "surat": (21.1702, 72.8311, "Gujarat"),
    "vadodara": (22.3072, 73.1812, "Gujarat"),
    "baroda": (22.3072, 73.1812, "Gujarat"),
    "rajkot": (22.3039, 70.8022, "Gujarat"),
    "gandhinagar": (23.2156, 72.6369, "Gujarat"),
    "bhavnagar": (21.7645, 72.1519, "Gujarat"),
    "jamnagar": (22.4707, 70.0577, "Gujarat"),
    "junagadh": (21.5222, 70.4579, "Gujarat"),
    "anand": (22.5645, 72.9289, "Gujarat"),
    "nadiad": (22.6939, 72.8618, "Gujarat"),
    "mehsana": (23.5880, 72.3693, "Gujarat"),
    # Haryana
    "gurugram": (28.4595, 77.0266, "Haryana"),
    "gurgaon": (28.4595, 77.0266, "Haryana"),
    "faridabad": (28.4089, 77.3178, "Haryana"),
    "panipat": (29.3909, 76.9635, "Haryana"),
    "ambala": (30.3782, 76.7767, "Haryana"),
    "karnal": (29.6857, 76.9905, "Haryana"),
    "hisar": (29.1492, 75.7217, "Haryana"),
    "rohtak": (28.8955, 76.6066, "Haryana"),
    "sonipat": (28.9931, 77.0151, "Haryana"),
    "yamunanagar": (30.1290, 77.2674, "Haryana"),
    # Himachal Pradesh
    "shimla": (31.1048, 77.1734, "Himachal Pradesh"),
    "solan": (30.9045, 77.0967, "Himachal Pradesh"),
    "dharamshala": (32.2190, 76.3234, "Himachal Pradesh"),
    "mandi": (31.7080, 76.9318, "Himachal Pradesh"),
    "una": (31.4685, 76.2708, "Himachal Pradesh"),
    "hamirpur": (31.6860, 76.5225, "Himachal Pradesh"),
    # Jharkhand
    "ranchi": (23.3441, 85.3096, "Jharkhand"),
    "jamshedpur": (22.8046, 86.2029, "Jharkhand"),
    "dhanbad": (23.7957, 86.4304, "Jharkhand"),
    "bokaro": (23.6693, 86.1511, "Jharkhand"),
    "deoghar": (24.4823, 86.6969, "Jharkhand"),
    "hazaribagh": (23.9925, 85.3637, "Jharkhand"),
    # Karnataka
    "bengaluru": (12.9716, 77.5946, "Karnataka"),
    "bangalore": (12.9716, 77.5946, "Karnataka"),
    "mysuru": (12.2958, 76.6394, "Karnataka"),
    "mysore": (12.2958, 76.6394, "Karnataka"),
    "hubli": (15.3647, 75.1240, "Karnataka"),
    "hubballi": (15.3647, 75.1240, "Karnataka"),
    "mangaluru": (12.9141, 74.8560, "Karnataka"),
    "mangalore": (12.9141, 74.8560, "Karnataka"),
    "belagavi": (15.8497, 74.4977, "Karnataka"),
    "belgaum": (15.8497, 74.4977, "Karnataka"),
    "davangere": (14.4644, 75.9218, "Karnataka"),
    "ballari": (15.1394, 76.9214, "Karnataka"),
    "bellary": (15.1394, 76.9214, "Karnataka"),
    "kalaburagi": (17.3297, 76.8343, "Karnataka"),
    "gulbarga": (17.3297, 76.8343, "Karnataka"),
    "shivamogga": (13.9299, 75.5681, "Karnataka"),
    "shimoga": (13.9299, 75.5681, "Karnataka"),
    "tumakuru": (13.3379, 77.1173, "Karnataka"),
    "udupi": (13.3409, 74.7421, "Karnataka"),
    "hospet": (15.2691, 76.3871, "Karnataka"),
    # Kerala
    "thiruvananthapuram": (8.5241, 76.9366, "Kerala"),
    "trivandrum": (8.5241, 76.9366, "Kerala"),
    "kochi": (9.9312, 76.2673, "Kerala"),
    "cochin": (9.9312, 76.2673, "Kerala"),
    "ernakulam": (9.9816, 76.2999, "Kerala"),
    "kozhikode": (11.2588, 75.7804, "Kerala"),
    "calicut": (11.2588, 75.7804, "Kerala"),
    "thrissur": (10.5276, 76.2144, "Kerala"),
    "kollam": (8.8932, 76.6141, "Kerala"),
    "kannur": (11.8745, 75.3704, "Kerala"),
    "kottayam": (9.5916, 76.5222, "Kerala"),
    "palakkad": (10.7867, 76.6548, "Kerala"),
    "alappuzha": (9.4981, 76.3388, "Kerala"),
    "malappuram": (11.0510, 76.0711, "Kerala"),
    "pathanamthitta": (9.2648, 76.7870, "Kerala"),
    # Madhya Pradesh
    "bhopal": (23.2599, 77.4126, "Madhya Pradesh"),
    "indore": (22.7196, 75.8577, "Madhya Pradesh"),
    "jabalpur": (23.1815, 79.9864, "Madhya Pradesh"),
    "gwalior": (26.2183, 78.1828, "Madhya Pradesh"),
    "ujjain": (23.1765, 75.7885, "Madhya Pradesh"),
    "sagar": (23.8388, 78.7378, "Madhya Pradesh"),
    "satna": (24.6005, 80.8322, "Madhya Pradesh"),
    "rewa": (24.5362, 81.3037, "Madhya Pradesh"),
    "ratlam": (23.3315, 75.0367, "Madhya Pradesh"),
    "dewas": (22.9676, 76.0534, "Madhya Pradesh"),
    # Maharashtra
    "mumbai": (19.0760, 72.8777, "Maharashtra"),
    "bombay": (19.0760, 72.8777, "Maharashtra"),
    "navi mumbai": (19.0330, 73.0297, "Maharashtra"),
    "thane": (19.2183, 72.9781, "Maharashtra"),
    "pune": (18.5204, 73.8567, "Maharashtra"),
    "nagpur": (21.1458, 79.0882, "Maharashtra"),
    "nashik": (19.9975, 73.7898, "Maharashtra"),
    "nasik": (19.9975, 73.7898, "Maharashtra"),
    "aurangabad": (19.8762, 75.3433, "Maharashtra"),
    "chhatrapati sambhajinagar": (19.8762, 75.3433, "Maharashtra"),
    "solapur": (17.6599, 75.9064, "Maharashtra"),
    "kolhapur": (16.7050, 74.2433, "Maharashtra"),
    "amravati": (20.9374, 77.7796, "Maharashtra"),
    "nanded": (19.1383, 77.3210, "Maharashtra"),
    "sangli": (16.8524, 74.5815, "Maharashtra"),
    "jalgaon": (21.0077, 75.5626, "Maharashtra"),
    "akola": (20.7002, 77.0082, "Maharashtra"),
    "latur": (18.4088, 76.5604, "Maharashtra"),
    "ahmednagar": (19.0948, 74.7480, "Maharashtra"),
    "satara": (17.6805, 74.0183, "Maharashtra"),
    "dombivli": (19.2094, 73.0870, "Maharashtra"),
    # Manipur
    "imphal": (24.8170, 93.9368, "Manipur"),
    # Meghalaya
    "shillong": (25.5788, 91.8933, "Meghalaya"),
    # Mizoram
    "aizawl": (23.7271, 92.7176, "Mizoram"),
    # Nagaland
    "kohima": (25.6751, 94.1086, "Nagaland"),
    "dimapur": (25.9091, 93.7266, "Nagaland"),
    # Odisha
    "bhubaneswar": (20.2961, 85.8245, "Odisha"),
    "cuttack": (20.4625, 85.8830, "Odisha"),
    "rourkela": (22.2604, 84.8536, "Odisha"),
    "berhampur": (19.3149, 84.7941, "Odisha"),
    "sambalpur": (21.4669, 83.9812, "Odisha"),
    "puri": (19.8135, 85.8312, "Odisha"),
    # Punjab
    "ludhiana": (30.9010, 75.8573, "Punjab"),
    "amritsar": (31.6340, 74.8723, "Punjab"),
    "jalandhar": (31.3260, 75.5762, "Punjab"),
    "patiala": (30.3398, 76.3869, "Punjab"),
    "bathinda": (30.2110, 74.9455, "Punjab"),
    "mohali": (30.7046, 76.7179, "Punjab"),
    "pathankot": (32.2643, 75.6421, "Punjab"),
    "hoshiarpur": (31.5322, 75.9119, "Punjab"),
    # Rajasthan
    "jaipur": (26.9124, 75.7873, "Rajasthan"),
    "jodhpur": (26.2389, 73.0243, "Rajasthan"),
    "udaipur": (24.5854, 73.7125, "Rajasthan"),
    "kota": (25.2138, 75.8648, "Rajasthan"),
    "ajmer": (26.4499, 74.6399, "Rajasthan"),
    "bikaner": (28.0229, 73.3119, "Rajasthan"),
    "alwar": (27.5530, 76.6346, "Rajasthan"),
    "bhilwara": (25.3407, 74.6313, "Rajasthan"),
    "sikar": (27.6094, 75.1399, "Rajasthan"),
    "sri ganganagar": (29.9038, 73.8772, "Rajasthan"),
    # Sikkim
    "gangtok": (27.3314, 88.6138, "Sikkim"),
    # Tamil Nadu
    "chennai": (13.0827, 80.2707, "Tamil Nadu"),
    "madras": (13.0827, 80.2707, "Tamil Nadu"),
    "coimbatore": (11.0168, 76.9558, "Tamil Nadu"),
    "madurai": (9.9252, 78.1198, "Tamil Nadu"),
    "tiruchirappalli": (10.7905, 78.7047, "Tamil Nadu"),
    "trichy": (10.7905, 78.7047, "Tamil Nadu"),
    "salem": (11.6643, 78.1460, "Tamil Nadu"),
    "tirunelveli": (8.7139, 77.7567, "Tamil Nadu"),
    "erode": (11.3410, 77.7172, "Tamil Nadu"),
    "vellore": (12.9165, 79.1325, "Tamil Nadu"),
    "thanjavur": (10.7870, 79.1378, "Tamil Nadu"),
    "tanjore": (10.7870, 79.1378, "Tamil Nadu"),
    "thoothukudi": (8.7642, 78.1348, "Tamil Nadu"),
    "tuticorin": (8.7642, 78.1348, "Tamil Nadu"),
    "dindigul": (10.3624, 77.9695, "Tamil Nadu"),
    "kanchipuram": (12.8342, 79.7036, "Tamil Nadu"),
    "cuddalore": (11.7480, 79.7714, "Tamil Nadu"),
    "karur": (10.9601, 78.0766, "Tamil Nadu"),
    "namakkal": (11.2189, 78.1677, "Tamil Nadu"),
    "hosur": (12.7409, 77.8253, "Tamil Nadu"),
    "nagercoil": (8.1780, 77.4342, "Tamil Nadu"),
    "tiruppur": (11.1085, 77.3411, "Tamil Nadu"),
    # Telangana
    "hyderabad": (17.3850, 78.4867, "Telangana"),
    "secunderabad": (17.4399, 78.4983, "Telangana"),
    "warangal": (17.9689, 79.5941, "Telangana"),
    "nizamabad": (18.6725, 78.0941, "Telangana"),
    "karimnagar": (18.4386, 79.1288, "Telangana"),
    "khammam": (17.2473, 80.1514, "Telangana"),
    "miyapur": (17.4969, 78.3428, "Telangana"),
    # Tripura
    "agartala": (23.8315, 91.2868, "Tripura"),
    # Uttar Pradesh
    "lucknow": (26.8467, 80.9462, "Uttar Pradesh"),
    "kanpur": (26.4499, 80.3319, "Uttar Pradesh"),
    "ghaziabad": (28.6692, 77.4538, "Uttar Pradesh"),
    "agra": (27.1767, 78.0081, "Uttar Pradesh"),
    "varanasi": (25.3176, 82.9739, "Uttar Pradesh"),
    "meerut": (28.9845, 77.7064, "Uttar Pradesh"),
    "prayagraj": (25.4358, 81.8463, "Uttar Pradesh"),
    "allahabad": (25.4358, 81.8463, "Uttar Pradesh"),
    "noida": (28.5355, 77.3910, "Uttar Pradesh"),
    "bareilly": (28.3670, 79.4304, "Uttar Pradesh"),
    "aligarh": (27.8974, 78.0880, "Uttar Pradesh"),
    "moradabad": (28.8386, 78.7733, "Uttar Pradesh"),
    "gorakhpur": (26.7606, 83.3732, "Uttar Pradesh"),
    "jhansi": (25.4484, 78.5685, "Uttar Pradesh"),
    "mathura": (27.4924, 77.6737, "Uttar Pradesh"),
    "ayodhya": (26.7994, 82.2043, "Uttar Pradesh"),
    "gautam buddha nagar": (28.5355, 77.3910, "Uttar Pradesh"),
    # Uttarakhand
    "dehradun": (30.3165, 78.0322, "Uttarakhand"),
    "haridwar": (29.9457, 78.1642, "Uttarakhand"),
    "haldwani": (29.2183, 79.5130, "Uttarakhand"),
    "rishikesh": (30.0869, 78.2676, "Uttarakhand"),
    "roorkee": (29.8543, 77.8880, "Uttarakhand"),
    "nainital": (29.3919, 79.4542, "Uttarakhand"),
    # West Bengal
    "kolkata": (22.5726, 88.3639, "West Bengal"),
    "calcutta": (22.5726, 88.3639, "West Bengal"),
    "howrah": (22.5958, 88.2636, "West Bengal"),
    "durgapur": (23.5204, 87.3119, "West Bengal"),
    "asansol": (23.6739, 86.9524, "West Bengal"),
    "siliguri": (26.7271, 88.3953, "West Bengal"),
    "kharagpur": (22.3460, 87.2320, "West Bengal"),
    "burdwan": (23.2324, 87.8615, "West Bengal"),
    "bardhaman": (23.2324, 87.8615, "West Bengal"),
    "malda": (25.0119, 88.1433, "West Bengal"),
    # Union territories
    "delhi": (28.7041, 77.1025, "Delhi"),
    "new delhi": (28.6139, 77.2090, "Delhi"),
    "chandigarh": (30.7333, 76.7794, "Chandigarh"),
    "puducherry": (11.9416, 79.8083, "Puducherry"),
    "pondicherry": (11.9416, 79.8083, "Puducherry"),
    "port blair": (11.6234, 92.7265, "Andaman and Nicobar Islands"),
    "jammu": (32.7266, 74.8570, "Jammu and Kashmir"),
    "srinagar": (34.0837, 74.7973, "Jammu and Kashmir"),
    "leh": (34.1526, 77.5770, "Ladakh"),
    "silvassa": (20.2666, 73.0166, "Dadra and Nagar Haveli and Daman and Diu"),
    "daman": (20.3974, 72.8328, "Dadra and Nagar Haveli and Daman and Diu"),
}


def _norm(s: Optional[str]) -> str:
    """Lowercase, strip punctuation noise and collapse whitespace."""
    if not s:
        return ""
    s = s.strip().strip(".").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def canonical_state(state: Optional[str], city: Optional[str] = None) -> Optional[str]:
    """Resolve an input state (with optional city hint) to a canonical name.

    Returns None when the value can't be mapped to an Indian state/UT — this is
    how foreign or garbage entries ("Arad", "Matosinhos") get dropped.
    """
    key = _norm(state)
    if key:
        if key in _CANONICAL_LOWER:
            return _CANONICAL_LOWER[key]
        if key in STATE_ALIASES:
            return STATE_ALIASES[key]
        # The scraped "state" is sometimes actually a city (e.g. AICF's
        # "Hyderabad"). Fall through to the city gazetteer below using the
        # bad state value itself as a city candidate.
        if key in CITY_GAZETTEER:
            return CITY_GAZETTEER[key][2]

    city_key = _extract_city_key(city)
    if city_key and city_key in CITY_GAZETTEER:
        return CITY_GAZETTEER[city_key][2]
    return None


def _extract_city_key(city: Optional[str]) -> Optional[str]:
    """Normalise a (possibly noisy) city string and try to find a gazetteer key.

    City fields range from clean ("Chennai") to venue noise ("MERY MATHA CHURCH
    FUNCTION HALL GUNTAKAL"). We try the whole normalised string, then each
    token, preferring a full match, so "... GUNTAKAL" still won't match but
    "Raipur CG" -> "raipur" will.
    """
    key = _norm(city)
    if not key:
        return None
    if key in CITY_GAZETTEER:
        return key
    # Try individual whitespace tokens and known city names appearing anywhere.
    tokens = key.split(" ")
    for tok in tokens:
        if tok in CITY_GAZETTEER:
            return tok
    # Last resort: any gazetteer city name that appears as a substring token
    # sequence (handles "raipur cg", "guwahati chess academy").
    for name in CITY_GAZETTEER:
        if " " in name and name in key:
            return name
    return None


def geocode(
    city: Optional[str], state: Optional[str]
) -> tuple[Optional[float], Optional[float], Optional[str]]:
    """Return (lat, lng, precision).

    precision is "city" when a gazetteer city matched, "state" when we fell back
    to the state centroid, or None when nothing resolved.
    """
    city_key = _extract_city_key(city)
    if city_key and city_key in CITY_GAZETTEER:
        lat, lng, _st = CITY_GAZETTEER[city_key]
        return lat, lng, "city"

    canon = canonical_state(state, city)
    if canon and canon in STATE_CENTROIDS:
        lat, lng = STATE_CENTROIDS[canon]
        return lat, lng, "state"

    return None, None, None
