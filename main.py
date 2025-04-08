import xml.etree.ElementTree as ET
import json
from pathlib import Path

"""--------------------------------------------------------------------------
Well‑Architect XML  ➜  Cadence JSON merger
--------------------------------------------------------------------------
This script now performs two tasks:

1. **parse_wa_xml()**
   • Opens a WellArchitect export file (WITSML 1.4.1.1 schema).
   • Extracts:
       – Operator name
       – Region
       – EPSG code **and** its CRS name
       – *TVD‑RP* elevation (value + units) + referenced datum
       – Surface slot latitude / longitude (`slot‑geog`)
       – **Survey stations** (each with MD, inclination, azimuth)
   • Returns them in a plain Python `dict`.

2. **merge_into_cadence_json()**
   • Opens a Cadence export JSON file.
   • Adds a top‑level block **"Well Architect Data"** with all the parsed
     metadata (leaving the original Cadence structure untouched).
   • Writes a new JSON file and prints where it was saved.

Only the Python standard library is required.
"""

# ---------------------------------------------------------------------------
# 1.  Well‑Architect XML  →  Python dict
# ---------------------------------------------------------------------------

def parse_wa_xml(xml_path: str) -> dict:
    """Extract selected metadata (plus survey stations) from WA XML."""

    ns = {"wa": "http://www.witsml.org/schemas/1series"}

    tree = ET.parse(xml_path)
    root = tree.getroot()

    out = {
        "operator": None,
        "region": None,
        "crsName": None,
        "epsgCode": None,
        "tvdRpElevation": None,
        "surfaceLocation": None,
        "surveys": []  # list of {md, inc, azi}
    }

    # ---- WELL‑LEVEL METADATA ----------------------------------------------
    well = root.find("wa:wells/wa:well", ns)
    if well is None:
        return out

    # Operator
    op = well.find("wa:operator", ns)
    if op is not None and op.text:
        out["operator"] = op.text.strip()

    # Region
    reg = well.find("wa:region", ns)
    if reg is not None and reg.text:
        out["region"] = reg.text.strip()

    # TVD‑RP elevation
    tvd_rp = well.find("wa:wellDatum[@uid='TVD-RP']", ns)
    if tvd_rp is not None:
        elev = tvd_rp.find("wa:elevation", ns)
        if elev is not None and elev.text:
            datum_code = elev.get("datum")
            datum_name = None
            if datum_code:
                name_elem = well.find(
                    f"wa:wellDatum[@uid='{datum_code}']/wa:name", ns)
                if name_elem is not None and name_elem.text:
                    datum_name = name_elem.text.strip()
            out["tvdRpElevation"] = {
                "value": float(elev.text),
                "uom": elev.get("uom"),
                "referencedDatum": datum_name or datum_code
            }

    # EPSG code + CRS name
    for crs in well.findall("wa:wellCRS", ns):
        name_crs = crs.find("wa:mapProjection/wa:nameCRS", ns)
        if name_crs is not None and name_crs.get("namingSystem") == "EPSG":
            code = name_crs.get("code")
            if code:
                out["epsgCode"] = code
                out["crsName"] = name_crs.text.strip() if name_crs.text else None
                break

    # Surface slot lat/long
    slot = well.find("wa:referencePoint/wa:location[@uid='slot-geog']", ns)
    if slot is not None:
        lat = slot.find("wa:latitude", ns)
        lon = slot.find("wa:longitude", ns)
        if lat is not None and lon is not None:
            out["surfaceLocation"] = {
                "latitude": float(lat.text),
                "longitude": float(lon.text)
            }

    # ---- SURVEY STATIONS ---------------------------------------------------
    # Take the first <trajectory> and harvest every <trajectoryStation>
    traj = root.find("wa:trajectorys/wa:trajectory", ns)
    if traj is not None:
        for st in traj.findall("wa:trajectoryStation", ns):
            md_elem = st.find("wa:md", ns)
            inc_elem = st.find("wa:incl", ns)
            azi_elem = st.find("wa:azi", ns)
            if md_elem is None or inc_elem is None or azi_elem is None:
                continue  # skip incomplete rows
            try:
                md = float(md_elem.text)
                inc = float(inc_elem.text)
                azi = float(azi_elem.text)
            except (TypeError, ValueError):
                continue  # skip bad data
            out["surveys"].append({"md": md, "inc": inc, "azi": azi})

    return out

# ---------------------------------------------------------------------------
# 2.  Merge metadata into Cadence JSON
# ---------------------------------------------------------------------------

def merge_into_cadence_json(src_json: str, wa_info: dict, dst_json: str) -> Path:
    """Insert a top‑level *Well Architect Data* block into Cadence JSON."""

    with open(src_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    wa_block = data["Well Architect Data"] = {}

    # Simple copy of scalar fields
    scalar_keys = [
        ("Operator", "operator"),
        ("Region", "region"),
        ("CRS Name", "crsName"),
        ("EPSG Code", "epsgCode"),
        ("TVD‑RP Elevation", "tvdRpElevation"),
        ("Surface Slot Lat/Lon", "surfaceLocation"),
    ]
    for label, key in scalar_keys:
        if wa_info.get(key) is not None:
            wa_block[label] = wa_info[key]

    # Add surveys only if we have at least one station
    if wa_info.get("surveys"):
        wa_block["Surveys"] = wa_info["surveys"]

    with open(dst_json, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return Path(dst_json).resolve()

# ---------------------------------------------------------------------------
# 3.  Command‑line entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    XML_FILE = "Gunto_Unit_2H_PWP_Rev-C.0.xml"
    CADENCE_JSON_IN = "CadenceExportExample.json"
    OUTPUT_JSON = "CadenceExport_with_WA.json"

    print("Parsing Well‑Architect XML …", end=" ")
    wa_meta = parse_wa_xml(XML_FILE)
    print("done.")

    print("Injecting data into Cadence JSON …", end=" ")
    out_path = merge_into_cadence_json(CADENCE_JSON_IN, wa_meta, OUTPUT_JSON)
    print("done.")

    print(f"\nMerged file written to: {out_path}\n")

#
