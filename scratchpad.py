import xml.etree.ElementTree as ET
import json
from pathlib import Path
from typing import List, Dict
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

# Optional DB import — only needed if you wire up ULSSDB
try:
    from sqlalchemy import create_engine, text
except ImportError:
    create_engine = None  # type: ignore
    text = None  # type: ignore

from urllib.parse import quote_plus

odbc_str = (
    "DRIVER={ODBC Driver 13 for SQL Server};"
    "SERVER=.\\BHIDE;"
    "DATABASE=ULSSDB;"
    "UID=bhi_admin;"
    "PWD=go4sql!2k;"
)
ULSSDB_CONN_STR = f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_str)}"

TS_TABLE = "ULSSDB.ULSS.TrajectoryStation"
WB_TABLE = "ULSSDB.Advantage.WELLBORE"

FT_PER_M = 3.28084
DEG_PER_RAD = 57.2958

def parse_wa_xml(xml_path: str) -> Dict:
    ns = {"wa": "http://www.witsml.org/schemas/1series"}
    root = ET.parse(xml_path).getroot()

    out: Dict = {
        "operator": None,
        "region": None,
        "crsName": None,
        "epsgCode": None,
        "tvdRpElevation": None,
        "surfaceLocation": None,
    }

    well = root.find("wa:wells/wa:well", ns)
    if well is None:
        return out

    if (op := well.find("wa:operator", ns)) is not None and op.text:
        out["operator"] = op.text.strip()
    if (reg := well.find("wa:region", ns)) is not None and reg.text:
        out["region"] = reg.text.strip()

    if (tvd_rp := well.find("wa:wellDatum[@uid='TVD-RP']", ns)) is not None:
        elev = tvd_rp.find("wa:elevation", ns)
        if elev is not None and elev.text:
            datum_code = elev.get("datum")
            datum_name = None
            if datum_code:
                name_elem = well.find(f"wa:wellDatum[@uid='{datum_code}']/wa:name", ns)
                if name_elem is not None and name_elem.text:
                    datum_name = name_elem.text.strip()
            out["tvdRpElevation"] = {
                "value": float(elev.text),
                "uom": elev.get("uom"),
                "referencedDatum": datum_name or datum_code,
            }

    for crs in well.findall("wa:wellCRS", ns):
        name_crs = crs.find("wa:mapProjection/wa:nameCRS", ns)
        if name_crs is not None and name_crs.get("namingSystem") == "EPSG":
            if (code := name_crs.get("code")):
                out["epsgCode"] = code
                out["crsName"] = name_crs.text.strip() if name_crs.text else None
                break

    slot = well.find("wa:referencePoint/wa:location[@uid='slot-geog']", ns)
    if slot is not None:
        lat = slot.find("wa:latitude", ns)
        lon = slot.find("wa:longitude", ns)
        if lat is not None and lon is not None:
            out["surfaceLocation"] = {"latitude": float(lat.text), "longitude": float(lon.text)}

    return out

def get_wellbore_lookup(conn_str: str) -> Dict[str, str]:
    if create_engine is None:
        return {}
    engine = create_engine(conn_str)
    sql = text(f"SELECT DISTINCT WLBR_NAME, WLBR_IDENTIFIER FROM {WB_TABLE} ORDER BY WLBR_NAME")
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
        return {str(r.WLBR_NAME).strip().lower(): str(r.WLBR_IDENTIFIER) for r in rows if r.WLBR_NAME}

def fetch_ulss_surveys(conn_str: str, wellbore_id: str) -> List[Dict]:
    if create_engine is None or not wellbore_id:
        return []
    engine = create_engine(conn_str, fast_executemany=True)
    sql = text(
        f"""
        SELECT  TS.MD * {FT_PER_M}                 AS md,
                TS.ModifiedInclination * {DEG_PER_RAD} AS inc,
                TS.ModifiedAzimuth     * {DEG_PER_RAD} AS azi,
                TS.SurveyType AS SurveyType,
                TS.status AS status
        FROM    {TS_TABLE} TS
        WHERE   TS.status IN ('Accepted', 'Tiein', 'Modified')
        AND     TS.WellboreId = :wbid
        ORDER BY TS.MD
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"wbid": wellbore_id}).fetchall()
        return [dict(r._mapping) for r in rows]

def merge_into_cadence_json(json_in: str, wa_info: Dict, ulss_surveys: List[Dict], json_out: str, wb_name: str, wb_id: str) -> Path:
    with open(json_in, "r", encoding="utf-8") as f:
        data = json.load(f)

    wa_block = data["Well Architect Data"] = {}
    for label, key in {
        "Operator": "operator",
        "Region": "region",
        "CRS Name": "crsName",
        "EPSG Code": "epsgCode",
        "TVD-RP Elevation": "tvdRpElevation",
        "Surface Slot Lat/Lon": "surfaceLocation",
    }.items():
        if wa_info.get(key) is not None:
            wa_block[label] = wa_info[key]

    if ulss_surveys:
        ulss_block = data["ULSS Survey Data"] = {}
        ulss_block["Wellbore Info"] = {
            "Name": wb_name,
            "ID": wb_id
        }
        ulss_block["Surveys"] = ulss_surveys

    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return Path(json_out).resolve()

def launch_ui():
    root = tk.Tk()
    root.title("Cadence + WellArchitect Merger")

    def browse(var: tk.StringVar, types):
        p = filedialog.askopenfilename(filetypes=types)
        if p:
            var.set(p)

    def run_merge():
        json_path, xml_path = json_var.get(), xml_var.get()
        wb_name = wb_var.get().strip()
        wb_id = wb_lookup.get(wb_name.lower())

        if not all([json_path, xml_path, wb_id]):
            messagebox.showerror("Missing info", "Please select all inputs, including Wellbore.")
            return

        if wb_id is None:
            messagebox.showerror("Wellbore Error", f"Could not find Wellbore ID for: {wb_name}")
            return

        try:
            wa_info = parse_wa_xml(xml_path)
            ulss = fetch_ulss_surveys(ULSSDB_CONN_STR, wb_id)
            out_path = merge_into_cadence_json(
                json_path, wa_info, ulss,
                Path(json_path).with_name(Path(json_path).stem + "_merged.json"),
                wb_name, wb_id
            )
            messagebox.showinfo("Success", f"Merged file written to:\n{out_path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    pad = {"padx": 4, "pady": 4}
    json_var, xml_var, wb_var = tk.StringVar(), tk.StringVar(), tk.StringVar()

    tk.Label(root, text="Cadence JSON file:").grid(row=0, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=json_var, width=50).grid(row=0, column=1, **pad)
    tk.Button(root, text="Browse…", command=lambda: browse(json_var, [("JSON", "*.json")])).grid(row=0, column=2, **pad)

    tk.Label(root, text="WellArchitect XML file:").grid(row=1, column=0, sticky="e", **pad)
    tk.Entry(root, textvariable=xml_var, width=50).grid(row=1, column=1, **pad)
    tk.Button(root, text="Browse…", command=lambda: browse(xml_var, [("XML", "*.xml")])).grid(row=1, column=2, **pad)

    tk.Label(root, text="Wellbore:").grid(row=2, column=0, sticky="e", **pad)
    wb_combo = ttk.Combobox(root, textvariable=wb_var, width=47, state="readonly")
    wb_combo.grid(row=2, column=1, **pad)

    wb_lookup: Dict[str, str] = {}
    try:
        wb_lookup = get_wellbore_lookup(ULSSDB_CONN_STR)
        wb_combo["values"] = list(wb_lookup.keys())
        if wb_lookup:
            wb_combo.current(0)
    except Exception:
        pass

    tk.Button(root, text="Process", command=run_merge, width=15).grid(row=3, column=1, pady=8)
    root.mainloop()

if __name__ == "__main__":
    launch_ui()
