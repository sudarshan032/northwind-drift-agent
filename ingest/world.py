"""Truth model for the fictional Northwind Health system.

`build_world(seed)` returns one internally consistent world as plain dicts
(JSON-serialisable). Every view (CMDB, discovery, observed, identity,
evidence) is derived from it in `ingest.views`; views only disagree where
`ingest.drift` deliberately injects drift.

World layout (all lists sorted deterministically):

    sites        [{id, name, type, subtype, state, city, tz, beds, demo}]
    subnets      [{cidr, site, kind, method}]           # scan coverage
    departments  [{id, site, code, name}]
    support_groups [{name, description}]
    cis          [{sys_id, name, class, label, site, department, ...}]
    rels         [{parent, child, type, failover, obs: [leg, ...]}]
    identity     {users, groups, roles, grants, memberships}
    changes      [{number, ...}]

A relationship's `obs` legs describe how the dependency shows up in observed
data (flows or netflow): who talks to whom, protocol, rate and daily profile.
"""

import hashlib
import ipaddress
import random

SEED = 42
SNAPSHOT_END = "2026-10-14T23:59:59Z"
WINDOW_DAYS = 14
DEMO_SITES = ("H03", "H17", "H44", "GCC-HYD")
CENTRAL_SITES = ("DC-PRI", "DC-DR", "cloud-us-west", "cloud-central-india")

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------


def rng_for(seed, *tags):
    """Independent deterministic RNG per subsystem (string seeds are stable)."""
    return random.Random(f"{seed}:" + ":".join(str(t) for t in tags))


def make_sys_id(seed, name):
    """ServiceNow-style 32 lowercase hex sys_id, stable per CI name."""
    return hashlib.md5(f"northwind:{seed}:{name}".encode()).hexdigest()


class IPPool:
    """Hands out sequential host addresses from a subnet."""

    def __init__(self, cidr, start=10):
        self.net = ipaddress.ip_network(cidr)
        self.next = start

    def take(self):
        ip = self.net.network_address + self.next
        if ip not in self.net or ip == self.net.broadcast_address:
            raise ValueError(f"subnet {self.net} exhausted")
        self.next += 1
        return str(ip)


class Serials:
    """Unique vendor-style serial numbers."""

    PATTERNS = {
        "Dell": ("", 7), "HPE": ("CZ", 8), "Cisco": ("FOC", 8), "Fortinet": ("FG", 12),
        "Cradlepoint": ("WA", 10), "Pure Storage": ("PCH", 9), "Lenovo": ("PF", 6),
        "HP": ("VNB", 7), "VMware": ("VMware-42 ", 16),
    }

    def __init__(self, rng):
        self.rng = rng
        self.used = set()

    def new(self, vendor):
        prefix, n = self.PATTERNS.get(vendor, ("SN", 10))
        alphabet = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        while True:
            body = "".join(self.rng.choice(alphabet) for _ in range(n))
            if vendor == "VMware":
                body = body.lower()
            s = prefix + body
            if s not in self.used:
                self.used.add(s)
                return s


# --------------------------------------------------------------------------
# Static reference data
# --------------------------------------------------------------------------

STATE_TZ = {"WA": -7, "OR": -7, "CA": -7, "TX": -5, "MT": -6, "NM": -6, "AK": -8}

# (id, city, state, tier) with tier L=large, C=community, A=critical access
HOSPITALS = [
    ("H01", "Everett", "WA", "L"), ("H02", "Tacoma", "WA", "L"), ("H03", "Seattle", "WA", "L"),
    ("H04", "Spokane", "WA", "L"), ("H05", "Olympia", "WA", "C"), ("H06", "Bellingham", "WA", "C"),
    ("H07", "Yakima", "WA", "C"), ("H08", "Vancouver", "WA", "C"), ("H09", "Kennewick", "WA", "C"),
    ("H10", "Wenatchee", "WA", "C"), ("H11", "Walla Walla", "WA", "C"), ("H12", "Colville", "WA", "A"),
    ("H13", "Ilwaco", "WA", "A"), ("H14", "Forks", "WA", "A"),
    ("H15", "Portland", "OR", "L"), ("H16", "Eugene", "OR", "C"), ("H17", "Portland", "OR", "C"),
    ("H18", "Medford", "OR", "C"), ("H19", "Bend", "OR", "C"), ("H20", "Salem", "OR", "C"),
    ("H21", "Newberg", "OR", "C"), ("H22", "Seaside", "OR", "A"), ("H23", "Hood River", "OR", "A"),
    ("H24", "Burns", "OR", "A"),
    ("H25", "Los Angeles", "CA", "L"), ("H26", "Burbank", "CA", "L"), ("H27", "Orange", "CA", "L"),
    ("H28", "Santa Monica", "CA", "C"), ("H29", "Mission Hills", "CA", "C"), ("H30", "Torrance", "CA", "C"),
    ("H31", "Fullerton", "CA", "C"), ("H32", "Mission Viejo", "CA", "C"), ("H33", "Napa", "CA", "C"),
    ("H34", "Eureka", "CA", "C"), ("H35", "Petaluma", "CA", "C"), ("H36", "Apple Valley", "CA", "C"),
    ("H37", "Lubbock", "TX", "L"), ("H38", "Amarillo", "TX", "C"), ("H39", "Plainview", "TX", "C"),
    ("H40", "Levelland", "TX", "A"), ("H41", "Brownfield", "TX", "A"),
    ("H42", "Missoula", "MT", "L"), ("H43", "Polson", "MT", "C"), ("H44", "Glasgow", "MT", "A"),
    ("H45", "Libby", "MT", "A"), ("H46", "Plentywood", "MT", "A"),
    ("H47", "Seward", "AK", "A"), ("H48", "Kodiak", "AK", "A"), ("H49", "Valdez", "AK", "A"),
    ("H50", "Raton", "NM", "A"), ("H51", "Clayton", "NM", "A"),
]

CITIES = {
    "WA": ["Seattle", "Spokane", "Tacoma", "Vancouver", "Bellevue", "Kent", "Everett", "Renton",
           "Federal Way", "Yakima", "Kirkland", "Bellingham", "Auburn", "Kennewick", "Redmond",
           "Marysville", "Pasco", "Lakewood", "Richland", "Shoreline", "Olympia", "Lacey", "Burien",
           "Bothell", "Edmonds", "Puyallup", "Lynnwood", "Bremerton", "Longview", "Wenatchee",
           "Mount Vernon", "Walla Walla", "Pullman", "Port Angeles", "Moses Lake", "Centralia",
           "Aberdeen", "Ellensburg", "Sequim", "Colville"],
    "OR": ["Portland", "Salem", "Eugene", "Gresham", "Hillsboro", "Bend", "Beaverton", "Medford",
           "Springfield", "Corvallis", "Albany", "Tigard", "Lake Oswego", "Grants Pass",
           "Oregon City", "McMinnville", "Tualatin", "West Linn", "Woodburn", "Roseburg",
           "Klamath Falls", "Newberg", "Hood River", "The Dalles", "Astoria", "Coos Bay",
           "Pendleton", "Seaside", "Ontario", "Burns"],
    "CA": ["Los Angeles", "Long Beach", "Santa Ana", "Anaheim", "Irvine", "Burbank", "Glendale",
           "Pasadena", "Torrance", "Orange", "Fullerton", "Santa Monica", "Mission Viejo",
           "Huntington Beach", "Costa Mesa", "Napa", "Petaluma", "Santa Rosa", "Eureka",
           "Apple Valley", "Victorville", "Hesperia", "Garden Grove", "Newport Beach",
           "Laguna Hills", "Sherman Oaks", "Encino", "San Pedro", "Redondo Beach", "Tustin",
           "Novato", "Sonoma", "Fortuna", "Arcata"],
    "TX": ["Lubbock", "Amarillo", "Plainview", "Levelland", "Brownfield", "Midland", "Odessa",
           "Big Spring", "Snyder", "Hereford", "Canyon", "Pampa", "Borger", "Dumas", "Slaton"],
    "MT": ["Missoula", "Billings", "Great Falls", "Bozeman", "Butte", "Helena", "Kalispell",
           "Polson", "Glasgow", "Libby", "Plentywood", "Hamilton", "Whitefish", "Havre",
           "Miles City", "Lewistown"],
    "AK": ["Anchorage", "Fairbanks", "Juneau", "Wasilla", "Palmer", "Kenai", "Soldotna", "Seward",
           "Kodiak", "Valdez", "Homer", "Ketchikan"],
    "NM": ["Albuquerque", "Santa Fe", "Las Cruces", "Roswell", "Farmington", "Gallup", "Raton",
           "Clayton", "Clovis", "Las Vegas", "Taos", "Tucumcari"],
}
CLINICS_PER_STATE = {"WA": 300, "OR": 200, "CA": 300, "TX": 70, "MT": 60, "AK": 40, "NM": 30}
NONACUTE_PER_STATE = {"WA": 240, "OR": 160, "CA": 240, "TX": 56, "MT": 48, "AK": 32, "NM": 24}

DEPT_NAMES = {
    "ED": "Emergency", "ICU": "Intensive Care", "RAD": "Radiology", "LAB": "Laboratory",
    "PHARM": "Pharmacy", "SURG": "Surgery", "MEDSURG": "Medical-Surgical", "ADM": "Admissions",
    "BILL": "Billing", "CARD": "Cardiology", "ONC": "Oncology", "MAT": "Maternity",
    "PEDS": "Pediatrics", "NICU": "Neonatal Intensive Care", "OUTPT": "Outpatient Services",
    "ORTHO": "Orthopedics", "NEURO": "Neurology", "PSYCH": "Psychiatry", "REHAB": "Rehabilitation",
    "ENDO": "Endoscopy", "DIAL": "Dialysis", "RESP": "Respiratory Therapy",
    "CATH": "Cardiac Catheterization", "IR": "Interventional Radiology",
    "HIM": "Health Information Management", "BIOMED": "Clinical Engineering", "TRAUMA": "Trauma",
    # clinics
    "PC": "Primary Care", "UC": "Urgent Care", "IMG": "Imaging", "WH": "Women's Health",
    "DERM": "Dermatology", "BH": "Behavioral Health", "PT": "Physical Therapy",
    # non-acute
    "HH": "Home Health", "HSP": "Hospice", "SNF": "Skilled Nursing", "SL": "Senior Living",
    "ADMIN": "Administration",
    # GCC
    "CLOUD": "Cloud Operations", "CLINAPPS": "Clinical Applications Support",
    "INTEG": "Integration Support", "CYBER": "Cybersecurity", "DBA": "Database Administration",
}

HOSPITAL_DEPTS = {
    "L": (["ED", "ICU", "RAD", "LAB", "PHARM", "SURG", "MEDSURG", "ADM", "BILL"],
          ["CARD", "ONC", "MAT", "PEDS", "NICU", "OUTPT", "ORTHO", "NEURO", "PSYCH", "REHAB",
           "ENDO", "DIAL", "RESP", "CATH", "IR", "HIM", "BIOMED", "TRAUMA"], (18, 24)),
    "C": (["ED", "ICU", "RAD", "LAB", "PHARM", "SURG", "MEDSURG", "ADM"],
          ["BILL", "CARD", "MAT", "PEDS", "OUTPT", "ORTHO", "REHAB", "RESP", "HIM", "ENDO"], (10, 14)),
    "A": (["ED", "MEDSURG", "LAB", "RAD", "PHARM"], ["OUTPT", "REHAB", "ADM"], (5, 7)),
}

NONACUTE_TYPES = [("home_health", "HH", 150), ("hospice", "HSP", 100), ("skilled_nursing", "SNF", 120),
                  ("senior_living", "SL", 130), ("rehab", "REHAB", 80),
                  ("behavioral_health", "BH", 100), ("admin_office", "ADMIN", 120)]

# Per tier inventory ranges (DATA_SPEC section 2), inclusive.
INVENTORY = {
    "L": dict(wan=(2, 2), fw=(2, 2), core=(4, 6), access=(30, 50), servers=(10, 14), array=(1, 1),
              ct=(2, 4), mri=(1, 3), xray=(5, 8), us=(8, 12), mon=(6, 10), pump=(1, 1), lab=(12, 20),
              ws=(600, 1000), prn=(100, 200), beds=(400, 900)),
    "C": dict(wan=(2, 2), fw=(1, 1), core=(2, 2), access=(10, 20), servers=(4, 7), array=(0, 1),
              ct=(1, 2), mri=(0, 1), xray=(2, 4), us=(3, 6), mon=(2, 4), pump=(1, 1), lab=(4, 8),
              ws=(150, 350), prn=(30, 60), beds=(100, 399)),
    # CAH: CMS limits critical access hospitals to 25 inpatient beds (research note R1).
    "A": dict(wan=(1, 1), fw=(1, 1), core=(1, 1), access=(3, 6), servers=(1, 2), array=(0, 0),
              ct=(0, 1), mri=(0, 0), xray=(1, 2), us=(1, 2), mon=(1, 1), pump=(0, 1), lab=(1, 3),
              ws=(40, 80), prn=(8, 15), beds=(12, 25)),
}

# ServiceNow class -> graph label (DATA_SPEC section 8). Names follow the
# ServiceNow CMDB data model (research note R6).
CLASS_LABEL = {
    "cmdb_ci_service_business": "BusinessService",
    "cmdb_ci_appl": "Application",
    "u_cmdb_ci_hl7_interface": "Interface",
    "cmdb_ci_db_mssql_instance": "Database", "cmdb_ci_db_ora_instance": "Database",
    "cmdb_ci_db_postgresql_instance": "Database", "cmdb_ci_db_instance": "Database",
    "cmdb_ci_vmware_instance": "VM", "cmdb_ci_vm_instance": "VM",
    "cmdb_ci_esx_server": "Host", "cmdb_ci_win_server": "Host", "cmdb_ci_linux_server": "Host",
    "cmdb_ci_availability_zone": "Host",
    "cmdb_ci_storage_server": "StorageArray", "cmdb_ci_cloud_storage_account": "StorageArray",
    "u_cmdb_ci_backup_target": "BackupTarget",
    "cmdb_ci_ip_router": "NetworkDevice", "cmdb_ci_ip_firewall": "NetworkDevice",
    "cmdb_ci_ip_switch": "NetworkDevice", "cmdb_ci_wap_network": "NetworkDevice",
    "cmdb_ci_ip_network": "NetworkDevice",
    "cmdb_ci_med_diagnostic_imaging": "MedicalDevice", "cmdb_ci_med_lab_equipment": "MedicalDevice",
    "cmdb_ci_med_patient_monitoring": "MedicalDevice", "cmdb_ci_med_therapeutic_device": "MedicalDevice",
    "cmdb_ci_computer": "Endpoint", "cmdb_ci_printer": "Endpoint",
    "u_cmdb_ci_endpoint_group": "EndpointGroup",
    "cmdb_ci_cloud_service_account": "ExternalService",
}

# Classes a network/passive/cloud scanner can see as individual devices.
DISCOVERABLE_CLASSES = {
    "cmdb_ci_vmware_instance", "cmdb_ci_vm_instance", "cmdb_ci_esx_server", "cmdb_ci_win_server",
    "cmdb_ci_linux_server", "cmdb_ci_storage_server", "u_cmdb_ci_backup_target",
    "cmdb_ci_ip_router", "cmdb_ci_ip_firewall", "cmdb_ci_ip_switch",
    "cmdb_ci_med_diagnostic_imaging", "cmdb_ci_med_lab_equipment", "cmdb_ci_med_patient_monitoring",
    "cmdb_ci_med_therapeutic_device", "cmdb_ci_computer", "cmdb_ci_printer",
}

CRIT = {1: "1 - most critical", 2: "2 - somewhat critical", 3: "3 - less critical", 4: "4 - not critical"}
# Recovery tiers (research note R5): tier -> (rpo_minutes, rto_hours, backup_policy)
TIERS = {0: (0, 1, "bp-tier0-daily-offsite"), 1: (15, 4, "bp-tier1-daily-offsite"),
         2: (240, 24, "bp-tier2-daily-offsite"), 3: (1440, 72, "bp-tier3-weekly-offsite")}

MODELS = {
    "router_l": ("Cisco", "Catalyst 8300-1N1S-6T", "IOS-XE", "17.9.4a"),
    "router_s": ("Cisco", "Catalyst 8200L-1N-4T", "IOS-XE", "17.9.4a"),
    "lte": ("Cradlepoint", "E300-C4D", "NetCloud OS", "7.23.40"),
    "fw_l": ("Fortinet", "FortiGate 600F", "FortiOS", "7.2.8"),
    "fw_m": ("Fortinet", "FortiGate 200F", "FortiOS", "7.2.8"),
    "fw_s": ("Fortinet", "FortiGate 60F", "FortiOS", "7.2.8"),
    "core": ("Cisco", "Catalyst 9500-48Y4C", "IOS-XE", "17.9.4a"),
    "access": ("Cisco", "Catalyst 9300-48P", "IOS-XE", "17.9.4a"),
    "access_s": ("Cisco", "Catalyst 9200-24P", "IOS-XE", "17.9.4a"),
    "esx": ("Dell", "PowerEdge R750", "VMware ESXi", "8.0 U2"),
    "phys_win": ("HPE", "ProLiant DL360 Gen10", "Windows Server", "2019"),
    "vm_win": ("VMware", "VMware Virtual Platform", "Windows Server", "2022"),
    "vm_lin": ("VMware", "VMware Virtual Platform", "Red Hat Enterprise Linux", "9.2"),
    "cloud_vm": ("Cloud", "General purpose 8 vCPU", "Red Hat Enterprise Linux", "9.2"),
    "array_c": ("Pure Storage", "FlashArray//X70", "Purity//FA", "6.5.3"),
    "array_l": ("Dell", "PowerStore 1200T", "PowerStoreOS", "3.6"),
    "bkp": ("Dell", "PowerProtect DD9400", "DDOS", "7.10"),
    "ct": [("Siemens Healthineers", "SOMATOM go.Top", "Windows 10 IoT Enterprise", "LTSC 2019"),
           ("GE HealthCare", "Revolution EVO", "Linux", "CentOS 7")],
    "mri": [("Siemens Healthineers", "MAGNETOM Vida", "Windows 10 IoT Enterprise", "LTSC 2019"),
            ("Philips", "Ingenia Ambition 1.5T", "Windows 10 IoT Enterprise", "LTSC 2019")],
    "xray": [("Carestream", "DRX-Evolution Plus", "Windows 10 IoT Enterprise", "LTSC 2016"),
             ("Fujifilm", "FDR Visionary Suite", "Windows 10 IoT Enterprise", "LTSC 2019")],
    "us": [("Philips", "EPIQ Elite", "Windows 10 IoT Enterprise", "LTSC 2019"),
           ("GE HealthCare", "LOGIQ E10", "Linux", "Yocto 3.1")],
    "mon": [("Philips", "IntelliVue Information Center iX", "Windows Server", "2016"),
            ("GE HealthCare", "CARESCAPE Gateway", "Linux", "QNX 7.0")],
    "pump": [("BD", "Alaris Systems Manager", "Windows Server", "2016"),
             ("Baxter", "Sigma Spectrum Server", "Windows Server", "2019")],
    "lab": [("Roche", "cobas pro c 503", "Windows 10 IoT Enterprise", "LTSC 2019"),
            ("Sysmex", "XN-1000", "Windows 10 IoT Enterprise", "LTSC 2016"),
            ("Abbott", "Alinity ci", "Windows 10 IoT Enterprise", "LTSC 2019"),
            ("Beckman Coulter", "DxH 900", "Windows 10 IoT Enterprise", "LTSC 2019")],
    "ws": ("Dell", "OptiPlex 7010", "Windows 11 Enterprise", "23H2"),
    "laptop": ("Lenovo", "ThinkPad T14 Gen 4", "Windows 11 Enterprise", "23H2"),
    "prn": ("HP", "LaserJet Enterprise M611", "HP FutureSmart", "5.7"),
}

# Local server roles (DATA_SPEC section 2). Infusion pump servers are vendor
# appliances, modelled as medical devices, so "pump server" is not a role here.
ROLE_INFO = {
    "pacs-cache": ("PACS cache", True, "IMG"), "mwl": ("Modality worklist broker", False, "IMG"),
    "labmw": ("Lab analyser middleware", True, "LABSYS"), "print": ("Print server", False, "SITE"),
    "nursecall": ("Nurse call server", False, "SITE"), "monitor-gw": ("Monitor gateway server", False, "SITE"),
    "rtls": ("Badge/RTLS server", False, "SITE"), "fs": ("Local file server", True, "SITE"),
    "emr-edge": ("Edge EMR cache (downtime)", True, "EMR"),
}
LARGE_ROLES = ["pacs-cache", "mwl", "labmw", "print", "nursecall", "monitor-gw", "rtls", "fs", "emr-edge"]
COMMUNITY_ROLES = ["pacs-cache", "labmw", "print", "monitor-gw", "emr-edge", "mwl", "fs"]
CAH_ROLES = ["print", "fs"]

# Jobs per department (identity groups are site x department x job).
DEPT_JOBS = {
    "ED": ["physician", "nurse", "unit-clerk"], "ICU": ["physician", "nurse"],
    "RAD": ["radiologist", "rad-tech"], "LAB": ["lab-tech", "pathologist"],
    "PHARM": ["pharmacist", "pharmacy-tech"], "ADM": ["registrar"], "BILL": ["coder"],
    "HIM": ["coder"], "REHAB": ["therapist"], "PT": ["therapist"], "RESP": ["therapist"],
    "BIOMED": ["biomed-tech"], "IMG": ["rad-tech"], "ADMIN": ["admin-clerk"],
    "HH": ["nurse", "therapist"], "HSP": ["nurse"], "SNF": ["nurse", "therapist"],
    "SL": ["caregiver"], "CLOUD": ["cloud-ops"], "CLINAPPS": ["clinical-apps-support"],
    "INTEG": ["integration-eng"], "CYBER": ["secops"], "DBA": ["dba"],
}
DEFAULT_JOBS = ["physician", "nurse"]


# --------------------------------------------------------------------------
# World builder
# --------------------------------------------------------------------------


class World:
    """Accumulates the truth model; `build_world` drives it."""

    def __init__(self, seed):
        self.seed = seed
        self.r = rng_for(seed, "world")
        self.serials = Serials(rng_for(seed, "serials"))
        self.sites, self.subnets, self.departments = [], [], []
        self.cis, self.rels, self.changes = [], [], []
        self.by_name = {}
        self.support_groups = {}
        self.site_by_id = {}

    # ---- primitives ------------------------------------------------------

    def rand(self, lo_hi, r=None):
        lo, hi = lo_hi
        return (r or self.r).randint(lo, hi)

    def group(self, name, description):
        self.support_groups.setdefault(name, description)
        return name

    def add_site(self, **kw):
        kw.setdefault("subtype", "")
        kw.setdefault("beds", 0)
        kw["demo"] = kw["id"] in DEMO_SITES
        kw["tz"] = kw.get("tz", STATE_TZ.get(kw.get("state"), 0))
        self.sites.append(kw)
        self.site_by_id[kw["id"]] = kw
        return kw

    def add_subnet(self, cidr, site, kind, method="active"):
        self.subnets.append({"cidr": cidr, "site": site, "kind": kind, "method": method})
        return IPPool(cidr, start=10 if ipaddress.ip_network(cidr).num_addresses > 64 else 1)

    def add_dept(self, site, code):
        d = {"id": f"{site}-{code}", "site": site, "code": code, "name": DEPT_NAMES[code]}
        self.departments.append(d)
        return d["id"]

    def ci(self, name, cls, site, owner, *, dept="", env="production", crit=3, phi=False,
           residency="US", vendor_model=None, ip="", tier=None, patchable=True, safety="none",
           count=None, data_holder=False, **extra):
        """Create one CI. `data_holder` CIs get backups and recovery tiers."""
        assert name not in self.by_name, name
        vendor, model, os_, os_ver = vendor_model or ("", "", "", "")
        serial = self.serials.new(vendor) if cls in DISCOVERABLE_CLASSES else ""
        rpo = rto = policy = ""
        if tier is not None:
            rpo, rto, policy = TIERS[tier]
        c = {
            "sys_id": make_sys_id(self.seed, name), "name": name, "class": cls,
            "label": CLASS_LABEL[cls], "site": site, "department": dept, "environment": env,
            "status": "operational", "support_group": owner, "criticality": CRIT[crit],
            "handles_phi": phi, "data_residency": residency, "serial_number": serial,
            "manufacturer": vendor, "os": os_, "os_version": os_ver, "model": model,
            "ip_address": ip, "rpo_minutes": rpo, "rto_hours": rto,
            "backup_policy": policy if data_holder else "", "backup_offsite": bool(data_holder),
            "patchable": patchable, "patient_safety_impact": safety,
            "count": count if count is not None else "",
            "discoverable": cls in DISCOVERABLE_CLASSES and count is None,
            "data_holder": data_holder,
            "message_type": "", "protocol": "", "source": "", "target": "", "via": "",
            "role": "",
        }
        c.update(extra)
        self.cis.append(c)
        self.by_name[name] = c
        return c

    def rel(self, parent, child, rtype, failover=False, obs=None, **extra):
        assert parent in self.by_name and child in self.by_name, (parent, child)
        r = {"parent": parent, "child": child, "type": rtype, "failover": failover, "obs": obs or []}
        r.update(extra)
        self.rels.append(r)
        return r

    def dep(self, parent, child, failover=False, **leg):
        """`parent` Depends on `child`. Unless failover, one observed leg
        (default direction parent -> child) is attached; override with
        src/dst in `leg`."""
        obs = []
        if not failover:
            leg.setdefault("src", parent)
            leg.setdefault("dst", child)
            obs.append(leg)
        return self.rel(parent, child, "Depends on::Used by", failover=failover, obs=obs)

    def service_ip(self, name):
        """IP a flow to/from this CI uses (app -> its first VM/host)."""
        c = self.by_name[name]
        if c["ip_address"]:
            return c["ip_address"]
        return c.get("service_ip", "")


def _tier_of(site):
    return {"large_hospital": "L", "community_hospital": "C", "critical_access_hospital": "A"}.get(site["type"])


def build_world(seed=SEED):
    """Build the complete deterministic truth model."""
    w = World(seed)
    _sites(w)
    _central(w)
    for s in [s for s in w.sites if _tier_of(s)]:
        _hospital(w, s)
    for s in [s for s in w.sites if s["type"] == "clinic"]:
        _clinic(w, s)
    for s in [s for s in w.sites if s["type"] == "non_acute"]:
        _nonacute(w, s)
    _gcc_site(w)
    for c in w.cis:  # declared protection: every data holder is backed up by the DC-PRI appliance
        if c["data_holder"]:
            w.rel(c["name"], "bkp-appl-pri-01", "Backed up by::Backs up")
    _identity(w)
    _changes(w)
    w.cis.sort(key=lambda c: c["name"])
    return {
        "meta": {"seed": seed, "snapshot_end": SNAPSHOT_END, "window_days": WINDOW_DAYS,
                 "demo_sites": list(DEMO_SITES), "fictional": True},
        "sites": w.sites, "subnets": w.subnets, "departments": w.departments,
        "support_groups": [{"name": k, "description": v} for k, v in sorted(w.support_groups.items())],
        "cis": w.cis, "rels": w.rels,
        "identity": w.identity, "changes": w.changes,
    }


# --------------------------------------------------------------------------
# Sites
# --------------------------------------------------------------------------


def _sites(w):
    r = rng_for(w.seed, "sites")
    for hid, city, state, tier in HOSPITALS:
        t = {"L": "large_hospital", "C": "community_hospital", "A": "critical_access_hospital"}[tier]
        beds = r.randint(*INVENTORY[tier]["beds"])
        w.add_site(id=hid, name=f"Northwind {city} {'Medical Center' if tier == 'L' else 'Hospital'}",
                   type=t, state=state, city=city, beds=beds)
    n = 0
    for state, count in CLINICS_PER_STATE.items():
        cities = CITIES[state]
        weights = [1 / (i + 1) ** 0.6 for i in range(len(cities))]
        for _ in range(count):
            n += 1
            city = r.choices(cities, weights)[0]
            w.add_site(id=f"C{n:04d}", name=f"Northwind {city} Clinic {n:04d}", type="clinic",
                       state=state, city=city)
    n = 0
    types = [(t, wgt) for t, _, wgt in NONACUTE_TYPES]
    for state, count in NONACUTE_PER_STATE.items():
        cities = CITIES[state]
        weights = [1 / (i + 1) ** 0.6 for i in range(len(cities))]
        for _ in range(count):
            n += 1
            city = r.choices(cities, weights)[0]
            sub = r.choices([t for t, _ in types], [x for _, x in types])[0]
            w.add_site(id=f"N{n:03d}", name=f"Northwind {city} {sub.replace('_', ' ').title()} {n:03d}",
                       type="non_acute", subtype=sub, state=state, city=city)
    w.add_site(id="DC-PRI", name="Northwind Primary Data Centre (rooms A/B)", type="data_centre",
               subtype="primary", state="WA", city="Quincy")
    w.add_site(id="DC-DR", name="Northwind DR Data Centre", type="data_centre", subtype="dr",
               state="TX", city="Lubbock", tz=-5)
    w.add_site(id="cloud-us-west", name="Cloud region us-west (zones a, b, c)", type="cloud_region",
               state="", city="", tz=-7)
    w.add_site(id="cloud-central-india", name="Cloud region central-india", type="cloud_region",
               state="", city="", tz=5.5)
    w.add_site(id="GCC-HYD", name="Northwind Global Capability Centre Hyderabad", type="gcc",
               state="", city="Hyderabad", tz=5.5)


# --------------------------------------------------------------------------
# Central systems (DATA_SPEC section 3)
# --------------------------------------------------------------------------


def _central(w):
    G = w.group
    infra = G("NW-Datacenter-Infra", "Data centre compute and virtualisation")
    stor = G("NW-Backup-Storage", "Storage arrays, replication and backup")
    net_dc = G("NW-Network-DC", "Data centre network")
    cloud = G("NW-Cloud-Platform", "Cloud landing zone and VMs")
    w.cloud_owner = cloud

    # --- networks / subnets
    pri = {"mgmt": w.add_subnet("10.10.0.0/24", "DC-PRI", "mgmt"),
           "host": w.add_subnet("10.10.1.0/24", "DC-PRI", "servers"),
           "stor": w.add_subnet("10.10.2.0/24", "DC-PRI", "storage"),
           "bkp": w.add_subnet("10.10.3.0/24", "DC-PRI", "backup"),
           "vm": w.add_subnet("10.10.4.0/22", "DC-PRI", "servers")}
    dr = {"mgmt": w.add_subnet("10.20.0.0/24", "DC-DR", "mgmt"),
          "host": w.add_subnet("10.20.1.0/24", "DC-DR", "servers"),
          "stor": w.add_subnet("10.20.2.0/24", "DC-DR", "storage"),
          "bkp": w.add_subnet("10.20.3.0/24", "DC-DR", "backup"),
          "vm": w.add_subnet("10.20.4.0/22", "DC-DR", "servers")}
    usw = {"vm": w.add_subnet("10.30.4.0/22", "cloud-us-west", "servers", method="cloud_api")}
    cin = {"vm": w.add_subnet("10.40.4.0/22", "cloud-central-india", "servers", method="cloud_api")}
    w.pools = {"DC-PRI": pri, "DC-DR": dr, "cloud-us-west": usw, "cloud-central-india": cin}

    # --- DC network
    for site, pool, pre in (("DC-PRI", pri, "dc1"), ("DC-DR", dr, "dc2")):
        for i in (1, 2):
            w.ci(f"{pre}-wan-0{i}", "cmdb_ci_ip_router", site, net_dc, crit=1,
                 vendor_model=MODELS["router_l"], ip=pool["mgmt"].take())
            w.ci(f"{pre}-fw-0{i}", "cmdb_ci_ip_firewall", site, net_dc, crit=1,
                 vendor_model=MODELS["fw_l"], ip=pool["mgmt"].take())
        for i in range(1, 5 if site == "DC-PRI" else 3):
            w.ci(f"{pre}-core-0{i}", "cmdb_ci_ip_switch", site, net_dc, crit=1,
                 vendor_model=MODELS["core"], ip=pool["mgmt"].take())
        cores = [c["name"] for c in w.cis if c["name"].startswith(f"{pre}-core-")]
        for i in (1, 2):
            w.rel(f"{pre}-fw-0{i}", f"{pre}-wan-0{i}", "Connects through::Connects")
            w.rel(cores[i - 1], f"{pre}-fw-0{i}", "Connects through::Connects")
        w.dc_cores = getattr(w, "dc_cores", {})
        w.dc_cores[site] = cores

    # --- storage arrays (room A, room B, DR) and cloud storage
    arrays = {}
    for room in ("a", "b"):
        for i in range(1, 5):
            n = f"arr-pri-{room}-0{i}"
            arrays[n] = w.ci(n, "cmdb_ci_storage_server", "DC-PRI", stor, crit=1,
                             vendor_model=MODELS["array_c"], ip=pri["stor"].take(), room=room.upper(),
                             raid_level="RAID6" if i == 4 else "RAID10", capacity_tb=400 if i == 4 else 180,
                             encrypted=True)
    for i in range(1, 7):
        n = f"arr-dr-0{i}"
        arrays[n] = w.ci(n, "cmdb_ci_storage_server", "DC-DR", stor, crit=2,
                         vendor_model=MODELS["array_c"], ip=dr["stor"].take(),
                         raid_level="RAID6", capacity_tb=400 if i in (4, 5) else 180, encrypted=True)
    for n, site in (("stor-usw-01", "cloud-us-west"), ("stor-cin-01", "cloud-central-india")):
        w.ci(n, "cmdb_ci_cloud_storage_account", site, cloud, crit=2,
             vendor_model=("Cloud", "Managed block storage (zone-redundant)", "", ""),
             residency="IN" if site == "cloud-central-india" else "US",
             raid_level="", capacity_tb=200, encrypted=True)
    # Array replication: sync room A -> room B, async room A -> DR.
    for i in range(1, 5):
        w.rel(f"arr-pri-a-0{i}", f"arr-pri-b-0{i}", "Replicates to::Replica of", mode="sync")
        w.rel(f"arr-pri-a-0{i}", f"arr-dr-0{i}", "Replicates to::Replica of", mode="async")
    w.rel("arr-pri-a-04", "arr-dr-05", "Replicates to::Replica of", mode="async")  # PACS second copy
    # D3 failover trap: reverse (failback) replication configured but idle until failover.
    w.rel("arr-dr-01", "arr-pri-a-01", "Replicates to::Replica of", failover=True, mode="async")

    # --- hosts
    w.hosts = {"DC-PRI": [], "DC-DR": []}
    for site, pool, pre, n in (("DC-PRI", pri, "dc1", 24), ("DC-DR", dr, "dc2", 10)):
        for i in range(1, n + 1):
            h = w.ci(f"{pre}-host-{i:02d}", "cmdb_ci_esx_server", site, infra, crit=1,
                     vendor_model=MODELS["esx"], ip=pool["host"].take(),
                     room=("A" if i <= n // 2 else "B") if site == "DC-PRI" else "")
            w.hosts[site].append(h["name"])
            if site == "DC-PRI":
                room = "a" if i <= n // 2 else "b"
                k = (i - 1) % 3 + 1
                w.rel(h["name"], f"arr-pri-{room}-0{k}", "Stores on::Stores")
            else:
                w.rel(h["name"], f"arr-dr-0{(i - 1) % 3 + 1}", "Stores on::Stores")
                if i <= 2:
                    w.rel(h["name"], "arr-dr-06", "Stores on::Stores")
            w.rel(h["name"], w.dc_cores[site][(i - 1) % len(w.dc_cores[site])], "Connects through::Connects")
    # Cloud availability zones act as the "host" layer for cloud VMs.
    w.zones = {}
    for site, zones in (("cloud-us-west", ["usw-az-a", "usw-az-b", "usw-az-c"]),
                        ("cloud-central-india", ["cin-az-1"])):
        w.zones[site] = zones
        for z in zones:
            w.ci(z, "cmdb_ci_availability_zone", site, cloud, crit=2,
                 vendor_model=("Cloud", "Availability zone", "", ""),
                 residency="IN" if site == "cloud-central-india" else "US")
            w.rel(z, "stor-usw-01" if site == "cloud-us-west" else "stor-cin-01", "Stores on::Stores")

    w.vm_counter = {}

    def vm(site, label, os_key="vm_win", host=None, owner=infra, crit=1, residency="US"):
        """One VM at a central site, round-robin across hosts unless pinned."""
        pre = {"DC-PRI": "dc1", "DC-DR": "dc2", "cloud-us-west": "usw", "cloud-central-india": "cin"}[site]
        name = f"{pre}-vm-{label}"
        cloud_site = site.startswith("cloud")
        cls = "cmdb_ci_vm_instance" if cloud_site else "cmdb_ci_vmware_instance"
        model = MODELS["cloud_vm"] if cloud_site else MODELS[os_key]
        c = w.ci(name, cls, site, cloud if cloud_site else owner, crit=crit, vendor_model=model,
                 ip=w.pools[site]["vm"].take(), residency=residency)
        if host is None:
            hosts = w.zones[site] if cloud_site else w.hosts[site]
            k = w.vm_counter.get(site, 0)
            w.vm_counter[site] = k + 1
            host = hosts[k % len(hosts)]
            if host == "dc1-host-07":  # keep dc1-host-07 for the lab results cache only
                host = hosts[(k + 1) % len(hosts)]
                w.vm_counter[site] = k + 2
        w.rel(name, host, "Runs on::Runs")
        return name

    w.vm = vm
    AP = {}

    def app(name, site, owner, vms, *, crit=1, phi=True, tier=None, env="production",
            residency="US", desc=""):
        c = w.ci(name, "cmdb_ci_appl", site, owner, crit=crit, phi=phi, env=env, residency=residency,
                 tier=tier, safety="high" if crit == 1 and phi else "low", description=desc)
        for v in vms:
            w.rel(name, v, "Runs on::Runs")
        c["service_ip"] = w.by_name[vms[0]]["ip_address"] if vms else ""
        AP[name] = c
        return name

    def db(name, site, owner, vm_name, array, *, cls="cmdb_ci_db_mssql_instance", crit=1, phi=True,
           tier=1, env="production", residency="US", model="Microsoft SQL Server 2019", gb=500, **extra):
        c = w.ci(name, cls, site, owner, crit=crit, phi=phi, env=env, residency=residency, tier=tier,
                 data_holder=True, vendor_model=("", model, "", ""),
                 safety="high" if crit == 1 and phi else "low", size_gb=gb, **extra)
        c["service_ip"] = w.by_name[vm_name]["ip_address"]
        w.rel(name, vm_name, "Runs on::Runs")
        w.rel(name, array, "Stores on::Stores")
        return name

    w.app, w.db = app, db

    emr = G("NW-EMR-Apps", "EMR application team")
    dba = G("NW-DBA", "Database administration")
    integ = G("NW-Integration", "Interface engine and HL7 routes")
    labs = G("NW-Lab-Systems", "LIS and lab middleware")
    img = G("NW-Imaging-Systems", "RIS, PACS and VNA")
    pharm = G("NW-Pharmacy-Systems", "Pharmacy information system")
    rev = G("NW-Revenue-Cycle-Apps", "Billing and revenue cycle")
    sched = G("NW-Access-Scheduling", "Scheduling and patient access")
    portal = G("NW-Digital-Portal", "Patient portal")
    data = G("NW-Data-Analytics", "Data warehouse and analytics")
    ident = G("NW-Identity", "Active Directory and SSO")
    secops = G("NW-SecOps", "Security operations (SIEM)")
    mon = G("NW-Monitoring", "Monitoring and observability")
    vendor = G("NW-Vendor-Management", "SaaS vendor management")
    hyd_int = G("HYD-Integration-Support", "GCC Hyderabad integration support")
    hyd_cloud = G("HYD-Cloud-Ops", "GCC Hyderabad cloud operations")
    hyd_clin = G("HYD-Clinical-Apps-Support", "GCC Hyderabad clinical applications support")
    hyd_cyber = G("HYD-Cybersecurity", "GCC Hyderabad cybersecurity")
    hyd_dev = G("HYD-DevTools", "GCC Hyderabad developer tooling and automation")
    w.owners = dict(emr=emr, dba=dba, integ=integ, labs=labs, img=img, infra=infra, hyd_int=hyd_int)

    # --- identity: AD domain controllers (VMs, two per US DC) and SSO
    ad = [vm("DC-PRI", "addc01", owner=ident), vm("DC-PRI", "addc02", owner=ident),
          vm("DC-DR", "addc01", owner=ident), vm("DC-DR", "addc02", owner=ident)]
    app("sso-app", "DC-PRI", ident, [vm("DC-PRI", "sso01", "vm_lin", owner=ident),
                                     vm("DC-PRI", "sso02", "vm_lin", owner=ident)],
        phi=False, crit=1, desc="Single sign-on (SAML/OIDC) for every application")
    for d in ad:
        w.dep("sso-app", d, protocol="ldaps", port=636, rate=900, profile="24x7")

    # --- external SaaS
    for n, desc in (("ext-email-saas", "Email and collaboration (SaaS)"),
                    ("ext-erp-hr-saas", "ERP / HR (SaaS) - source of user employment status"),
                    ("ext-itsm-saas", "Ticketing / ITSM (SaaS)"),
                    ("ext-claims-clearinghouse", "External claims clearinghouse")):
        c = w.ci(n, "cmdb_ci_cloud_service_account", "EXTERNAL", vendor, crit=2,
                 phi=n == "ext-claims-clearinghouse", description=desc)
        c["service_ip"] = f"203.0.113.{10 + len([x for x in w.cis if x['class'] == c['class']])}"  # TEST-NET-3
    w.dep("sso-app", "ext-erp-hr-saas", protocol="https", msg_type="SCIM", rate=40, profile="business")

    # --- EMR
    emr_vms = [vm("DC-PRI", f"emr{i:02d}", owner=emr) for i in range(1, 9)]
    app("emr-app", "DC-PRI", emr, emr_vms, tier=0, desc="Enterprise EMR (single instance for all sites)")
    db("emr-db-01", "DC-PRI", dba, vm("DC-PRI", "emrdb01", "vm_lin"), "arr-pri-a-01",
       cls="cmdb_ci_db_instance", tier=0, model="InterSystems IRIS 2024.1", gb=24000)
    db("emr-db-dr", "DC-DR", dba, vm("DC-DR", "emrdb01", "vm_lin"), "arr-dr-01",
       cls="cmdb_ci_db_instance", tier=0, model="InterSystems IRIS 2024.1", gb=24000)
    w.dep("emr-app", "emr-db-01", protocol="tcp-superserver", port=1972, rate=60000, profile="24x7")
    w.dep("emr-app", "emr-db-dr", failover=True)                           # D3 failover trap
    w.dep("emr-app", "sso-app", protocol="saml", rate=1800, profile="24x7")
    w.dep("emr-app", "ext-email-saas", protocol="smtp", port=587, rate=120, profile="24x7")

    # --- interface engine (2-node cluster) and its message store
    ie_vms = [vm("DC-PRI", "ie01", "vm_lin", owner=integ), vm("DC-PRI", "ie02", "vm_lin", owner=integ)]
    app("ie-app", "DC-PRI", integ, ie_vms, tier=0, desc="Interface engine (HL7 v2 / FHIR), 2-node cluster")
    w.by_name["ie-app"]["service_ip"] = "10.10.4.5"  # cluster VIP
    db("ie-db-01", "DC-PRI", hyd_int, vm("DC-PRI", "iedb01"), "arr-pri-a-02", tier=1, gb=900)
    w.dep("ie-app", "ie-db-01", protocol="tds", port=1433, rate=9000, profile="24x7")
    w.dep("ie-app", "sso-app", protocol="saml", rate=20, profile="24x7")
    app("ie-app-dr", "DC-DR", integ, [vm("DC-DR", "ie01", "vm_lin", owner=integ)], tier=0,
        desc="Interface engine DR node (cold standby)")
    w.dep("ie-app", "ie-app-dr", failover=True)                            # D3 failover trap

    # --- lab results cache: the headline database, pinned to dc1-host-07
    lrc_vm = vm("DC-PRI", "lrc01", host="dc1-host-07")
    # Day-to-day support of lrc-db-01 moved to the Hyderabad integration team (see D5 drift).
    db("lrc-db-01", "DC-PRI", hyd_int, lrc_vm, "arr-pri-a-02", tier=1, gb=350,
       description="Lab results cache: stages critical lab results for the interface engine")

    # --- LIS
    app("lis-app", "DC-PRI", labs, [vm("DC-PRI", f"lis{i:02d}", owner=labs) for i in (1, 2, 3)],
        tier=1, desc="Laboratory information system")
    db("lis-db-01", "DC-PRI", dba, vm("DC-PRI", "lisdb01"), "arr-pri-a-02", tier=1, gb=3200)
    db("lis-db-dr", "DC-DR", dba, vm("DC-DR", "lisdb01"), "arr-dr-02", tier=1, gb=3200)
    w.dep("lis-app", "lis-db-01", protocol="tds", port=1433, rate=12000, profile="24x7")
    # lis-db-dr is fed by array replication (arr-pri-a-02 -> arr-dr-02); no app-level failover link.
    w.dep("lis-app", "sso-app", protocol="saml", rate=300, profile="24x7")
    app("lis-reporting", "DC-PRI", labs, [vm("DC-PRI", "lisrpt01", owner=labs)], crit=3, tier=2,
        desc="LIS reporting (turnaround-time and critical-call reports)")
    w.dep("lis-reporting", "lis-db-01", protocol="tds", port=1433, rate=400, profile="business", tz=-7)
    w.dep("lis-reporting", "lrc-db-01", protocol="tds", port=1433, rate=60, profile="business", tz=-7)
    w.dep("lis-reporting", "sso-app", protocol="saml", rate=15, profile="business", tz=-7)
    app("labmw-central", "DC-PRI", labs, [vm("DC-PRI", "labmw01", owner=labs)], tier=1,
        desc="Central lab analyser middleware for sites without local middleware")
    db("labmw-central-db", "DC-PRI", dba, "dc1-vm-labmw01", "arr-pri-a-02", tier=1, gb=400)
    w.dep("labmw-central", "labmw-central-db", protocol="tds", port=1433, rate=2500, profile="24x7")
    w.dep("labmw-central", "sso-app", protocol="saml", rate=10, profile="24x7")

    # --- RIS / PACS / VNA
    app("ris-app", "DC-PRI", img, [vm("DC-PRI", f"ris{i:02d}", owner=img) for i in (1, 2)], tier=1,
        desc="Radiology information system (orders, worklists)")
    db("ris-db-01", "DC-PRI", dba, vm("DC-PRI", "risdb01"), "arr-pri-a-03", tier=1, gb=1500)
    w.dep("ris-app", "ris-db-01", protocol="tds", port=1433, rate=5000, profile="24x7")
    w.dep("ris-app", "sso-app", protocol="saml", rate=150, profile="24x7")
    app("pacs-app", "DC-PRI", img, [vm("DC-PRI", f"pacs{i:02d}", "vm_lin", owner=img) for i in (1, 2, 3)],
        tier=1, desc="Enterprise PACS")
    db("pacs-db-01", "DC-PRI", dba, vm("DC-PRI", "pacsdb01"), "arr-pri-a-03", tier=1, gb=2500)
    vna = w.ci("pacs-vna-01", "cmdb_ci_appl", "DC-PRI", img, crit=1, phi=True, tier=1, data_holder=True,
               safety="medium", description="Vendor neutral archive (images)", size_gb=900000)
    vna["service_ip"] = w.by_name[vm("DC-PRI", "vna01", "vm_lin", owner=img)]["ip_address"]
    w.rel("pacs-vna-01", "dc1-vm-vna01", "Runs on::Runs")
    w.rel("pacs-vna-01", "arr-pri-a-04", "Stores on::Stores")
    w.dep("pacs-app", "pacs-db-01", protocol="tds", port=1433, rate=8000, profile="24x7")
    w.dep("pacs-app", "pacs-vna-01", protocol="dicom", port=104, rate=3000, profile="24x7")
    w.dep("pacs-app", "sso-app", protocol="saml", rate=200, profile="24x7")
    vna_dr = w.ci("pacs-vna-dr", "cmdb_ci_appl", "DC-DR", img, crit=1, phi=True, tier=1,
                  safety="medium", description="VNA DR instance (standby)")
    vna_dr["service_ip"] = w.by_name[vm("DC-DR", "vna01", "vm_lin", owner=img)]["ip_address"]
    w.rel("pacs-vna-dr", "dc2-vm-vna01", "Runs on::Runs")
    w.rel("pacs-vna-dr", "arr-dr-04", "Stores on::Stores")
    w.dep("pacs-app", "pacs-vna-dr", failover=True)                        # D3 failover trap

    # --- pharmacy, billing, scheduling
    app("pharm-app", "DC-PRI", pharm, [vm("DC-PRI", f"pharm{i:02d}", owner=pharm) for i in (1, 2)],
        tier=1, desc="Pharmacy information system")
    db("pharm-db-01", "DC-PRI", dba, vm("DC-PRI", "pharmdb01"), "arr-pri-a-03", tier=1, gb=800)
    w.dep("pharm-app", "pharm-db-01", protocol="tds", port=1433, rate=6000, profile="24x7")
    w.dep("pharm-app", "sso-app", protocol="saml", rate=120, profile="24x7")
    w.dep("pharm-app", "lis-db-01", protocol="tds", port=1433, rate=300, profile="24x7")  # D1 candidate
    app("billing-app", "DC-PRI", rev, [vm("DC-PRI", f"bill{i:02d}", owner=rev) for i in (1, 2)],
        crit=2, tier=2, desc="Billing / revenue cycle")
    db("billing-db-01", "DC-PRI", dba, vm("DC-PRI", "billdb01", "vm_lin"), "arr-pri-a-03",
       cls="cmdb_ci_db_ora_instance", crit=2, tier=2, model="Oracle Database 19c", gb=4200)
    w.dep("billing-app", "billing-db-01", protocol="oracle-net", port=1521, rate=4000, profile="business", tz=-7)
    w.dep("billing-app", "ext-claims-clearinghouse", protocol="sftp", port=22, msg_type="X12 837",
          rate=30, profile="business", tz=-7)
    w.dep("billing-app", "sso-app", protocol="saml", rate=60, profile="business", tz=-7)
    w.dep("billing-app", "emr-db-01", protocol="tcp-superserver", port=1972, rate=200,
          profile="nightly", tz=-7)                                         # D1 candidate
    app("sched-app", "DC-PRI", sched, [vm("DC-PRI", f"sched{i:02d}", owner=sched) for i in (1, 2)],
        crit=2, tier=2, desc="Enterprise scheduling")
    db("sched-db-01", "DC-PRI", dba, vm("DC-PRI", "scheddb01"), "arr-pri-a-03", crit=2, tier=2, gb=600)
    w.dep("sched-app", "sched-db-01", protocol="tds", port=1433, rate=3000, profile="business", tz=-7)
    w.dep("sched-app", "sso-app", protocol="saml", rate=80, profile="business", tz=-7)

    # --- cloud-us-west: portal, analytics, SIEM, monitoring
    app("portal-app", "cloud-us-west", hyd_cloud,
        [vm("cloud-us-west", "portal01", host="usw-az-a"), vm("cloud-us-west", "portal02", host="usw-az-b")],
        crit=2, tier=2, desc="Patient portal (zones a/b)")
    db("portal-db-01", "cloud-us-west", dba, vm("cloud-us-west", "portaldb01", host="usw-az-a"),
       "stor-usw-01", cls="cmdb_ci_db_postgresql_instance", crit=2, tier=2, model="PostgreSQL 15", gb=300)
    w.dep("portal-app", "portal-db-01", protocol="postgres", port=5432, rate=2500, profile="24x7")
    w.dep("portal-app", "emr-app", protocol="https", msg_type="FHIR R4", rate=900, profile="24x7")
    w.dep("portal-app", "sso-app", protocol="oidc", rate=500, profile="24x7")
    w.dep("portal-app", "sched-app", protocol="https", msg_type="REST", rate=120,
          profile="24x7")                                                    # D1 candidate
    app("dwh-etl-app", "cloud-us-west", hyd_cloud, [vm("cloud-us-west", "dwhetl01", host="usw-az-a")],
        crit=3, tier=3, desc="Analytics ETL: nightly extracts from EMR")
    db("dwh-db-01", "cloud-us-west", dba, vm("cloud-us-west", "dwhdb01", host="usw-az-b"),
       "stor-usw-01", cls="cmdb_ci_db_postgresql_instance", crit=3, tier=3, model="PostgreSQL 15",
       gb=18000)
    w.dep("dwh-etl-app", "emr-db-01", protocol="tcp-superserver", port=1972, msg_type="extract",
          rate=8000, profile="nightly", tz=-7)
    w.dep("dwh-etl-app", "dwh-db-01", protocol="postgres", port=5432, rate=8000, profile="nightly", tz=-7)
    w.dep("dwh-etl-app", "pacs-db-01", protocol="tds", port=1433, msg_type="extract", rate=500,
          profile="nightly", tz=-7)                                          # D1 candidate
    w.dep("dwh-etl-app", "sso-app", protocol="oidc", rate=2, profile="nightly", tz=-7)
    app("siem-app", "cloud-us-west", hyd_cyber, [vm("cloud-us-west", "siem01", host="usw-az-c")],
        crit=2, phi=False, tier=2, desc="SIEM")
    w.dep("sso-app", "siem-app", protocol="syslog-tls", port=6514, rate=3000, profile="24x7")
    w.dep("emr-app", "siem-app", protocol="syslog-tls", port=6514, rate=6000, profile="24x7")
    for d in ad:
        w.dep(d, "siem-app", protocol="syslog-tls", port=6514, rate=1500, profile="24x7")
    w.dep("siem-app", "sso-app", protocol="oidc", rate=5, profile="24x7")
    app("mon-app", "cloud-us-west", mon, [vm("cloud-us-west", "mon01", host="usw-az-c")],
        crit=2, phi=False, desc="Monitoring and alerting")
    w.dep("mon-app", "ext-itsm-saas", protocol="https", msg_type="REST", rate=25, profile="24x7")
    w.dep("mon-app", "sso-app", protocol="oidc", rate=3, profile="24x7")

    # --- backup: media servers, appliance, DR appliance, immutable cloud vault
    bkp_vms = [vm("DC-PRI", "bkp01", owner=stor), vm("DC-PRI", "bkp02", owner=stor)]
    app("bkp-app", "DC-PRI", stor, bkp_vms, crit=1, phi=False, desc="Backup software (media servers)")
    w.ci("bkp-appl-pri-01", "u_cmdb_ci_backup_target", "DC-PRI", stor, crit=1,
         vendor_model=MODELS["bkp"], ip=pri["bkp"].take(), immutable=False)
    w.ci("bkp-appl-dr-01", "u_cmdb_ci_backup_target", "DC-DR", stor, crit=2,
         vendor_model=MODELS["bkp"], ip=dr["bkp"].take(), immutable=False)
    v = w.ci("bkp-vault-usw-01", "u_cmdb_ci_backup_target", "cloud-us-west", stor, crit=1,
             vendor_model=("Cloud", "Immutable object vault (object lock)", "", ""), immutable=True)
    v["discoverable"] = False
    w.dep("bkp-app", "bkp-appl-pri-01", protocol="ddboost", port=2049, rate=500, profile="nightly", tz=-7)
    w.dep("bkp-app", "sso-app", protocol="saml", rate=2, profile="24x7")
    w.rel("bkp-appl-pri-01", "bkp-appl-dr-01", "Replicates to::Replica of", mode="async")
    w.rel("bkp-appl-pri-01", "bkp-vault-usw-01", "Replicates to::Replica of", mode="async")
    w.rel("bkp-appl-pri-01", w.dc_cores["DC-PRI"][0], "Connects through::Connects")
    w.rel("bkp-appl-dr-01", w.dc_cores["DC-DR"][0], "Connects through::Connects")
    for a in arrays.values():
        w.rel(a["name"], w.dc_cores[a["site"]][0], "Connects through::Connects")

    # --- GCC tooling in cloud-central-india (never holds patient data)
    app("gcc-automation-app", "cloud-central-india", hyd_dev,
        [vm("cloud-central-india", "auto01", residency="IN")], crit=3, phi=False, env="production",
        residency="IN", desc="GCC automation and runbooks")
    app("gcc-devtest-app", "cloud-central-india", hyd_dev,
        [vm("cloud-central-india", "devtest01", residency="IN")], crit=4, phi=False, env="test",
        residency="IN", desc="GCC dev/test tooling (synthetic data only)")
    db("gcc-tools-db-01", "cloud-central-india", hyd_dev, vm("cloud-central-india", "toolsdb01", residency="IN"),
       "stor-cin-01", cls="cmdb_ci_db_postgresql_instance", crit=4, phi=False, tier=3, env="test",
       residency="IN", model="PostgreSQL 15", gb=200)
    w.dep("gcc-automation-app", "ext-itsm-saas", protocol="https", msg_type="REST", rate=60, profile="24x7")
    w.dep("gcc-automation-app", "mon-app", protocol="https", msg_type="REST", rate=30, profile="24x7")
    w.dep("gcc-devtest-app", "gcc-tools-db-01", protocol="postgres", port=5432, rate=200,
          profile="business", tz=5.5)

    # --- business services: logical groupings (Contains, not traffic-observable)
    w.business = {}
    for svc, apps_ in (("svc-clinical-documentation", ["emr-app"]), ("svc-lab-results", ["lis-app", "lis-reporting"]),
                       ("svc-imaging", ["pacs-app", "ris-app"]), ("svc-medication", ["pharm-app"]),
                       ("svc-revenue-cycle", ["billing-app"]), ("svc-scheduling", ["sched-app"]),
                       ("svc-patient-portal", ["portal-app"]), ("svc-analytics", ["dwh-etl-app"]),
                       ("svc-identity", ["sso-app"]), ("svc-integration", ["ie-app"])):
        w.ci(svc, "cmdb_ci_service_business", "DC-PRI", w.by_name[apps_[0]]["support_group"], crit=1,
             phi=False)
        for a in apps_:
            w.rel(svc, a, "Contains::Contained by")

    # --- enterprise HL7 interfaces (EMR <-> central systems)
    w.interfaces = []
    for name, mt, src, dst, rate in (
            ("ADT-EMR-LIS", "ADT^A01/A02/A03/A04/A08", "emr-app", "lis-app", 900),
            ("ADT-EMR-RIS", "ADT^A01/A02/A03/A04/A08", "emr-app", "ris-app", 900),
            ("ADT-EMR-PHARM", "ADT^A01/A02/A03/A04/A08", "emr-app", "pharm-app", 900),
            ("ADT-EMR-BILL", "ADT^A01/A03/A04/A08", "emr-app", "billing-app", 600),
            ("ADT-EMR-SCHED", "ADT^A04/A08/A40", "emr-app", "sched-app", 300),
            ("ORM-EMR-LIS", "OML^O21", "emr-app", "lis-app", 1500),
            ("ORM-EMR-RIS", "ORM^O01", "emr-app", "ris-app", 500),
            ("ORU-LIS-EMR", "ORU^R01", "lis-app", "emr-app", 4000),
            ("ORU-RIS-EMR", "ORU^R01", "ris-app", "emr-app", 450),
            ("RDE-EMR-PHARM", "RDE^O11", "emr-app", "pharm-app", 1200),
            ("DFT-EMR-BILL", "DFT^P03", "emr-app", "billing-app", 2500),
            ("MDM-RIS-EMR", "MDM^T02", "ris-app", "emr-app", 400),
            ("SIU-SCHED-EMR", "SIU^S12", "sched-app", "emr-app", 350)):
        _interface(w, name, mt, "MLLP", src, dst, rate, "24x7", crit=1 if mt.startswith(("ORU", "RDE", "OML")) else 2)


def _interface(w, name, mt, protocol, src, dst, rate, profile, crit=1, staging=None, tz=-7,
               src_leg=True, owner=None, via="ie-app", site_for_flow=None):
    """Interface CI with declared Depends on to source and target; observed
    as route-level legs src -> route -> target emitted by the transport."""
    via_ci = w.by_name.get(via)
    c = w.ci(name, "u_cmdb_ci_hl7_interface", "DC-PRI", owner or w.owners["integ"], crit=crit, phi=True,
             safety="high" if crit == 1 else "medium", message_type=mt, protocol=protocol,
             source=src, target=dst, via=via)
    c["service_ip"] = via_ci.get("service_ip", "") if via_ci else w.service_ip(dst)
    if via_ci is not None and via_ci["class"] == "cmdb_ci_appl":
        w.rel(via, name, "Contains::Contained by")
    leg = dict(protocol=protocol.lower(), msg_type=mt.split("/")[0] if "/" in mt else mt, rate=rate,
               profile=profile, tz=tz, route=name)
    if src_leg:
        w.dep(name, src, src=src, dst=name, **leg)
    w.dep(name, dst, src=name, dst=dst, **leg)
    if staging:
        w.dep(name, staging, src=name, dst=staging, **dict(leg, protocol="tds", port=1433))
    w.interfaces.append(name)
    return name


# --------------------------------------------------------------------------
# Hospitals
# --------------------------------------------------------------------------


def _hospital(w, s):
    sid, lo = s["id"], s["id"].lower()
    tier = _tier_of(s)
    inv = INVENTORY[tier]
    r = rng_for(w.seed, "site", sid)
    n = int(sid[1:])
    base = f"10.{100 + n}"
    mgmt = w.add_subnet(f"{base}.0.0/24", sid, "mgmt")
    srv = w.add_subnet(f"{base}.1.0/24", sid, "servers")
    med = w.add_subnet(f"{base}.8.0/21", sid, "medical", method="passive")
    usr_cidr = f"{base}.16.0/20"
    usr = w.add_subnet(usr_cidr, sid, "users", method="active" if s["demo"] else "aggregate")
    state = s["state"]
    net = w.group(f"NW-Network-{state}", f"Network operations, {state}")
    biomed = w.group(f"NW-Clinical-Engineering-{state}", f"Clinical engineering (biomed), {state}")
    field = w.group(f"NW-Field-Services-{state}", f"End-user and field services, {state}")
    site_it = w.group(f"NW-Site-IT-{sid}", f"Site IT, {s['name']}") if tier == "L" else \
        w.group(f"NW-Regional-Infra-{state}", f"Regional infrastructure, {state}")

    # departments
    core, pool, (dlo, dhi) = HOSPITAL_DEPTS[tier]
    k = r.randint(dlo, dhi) - len(core)
    codes = core + r.sample(pool, min(k, len(pool)))
    depts = {c: w.add_dept(sid, c) for c in codes}

    # network
    big = tier != "A"
    wans = [w.ci(f"{lo}-wan-{i:02d}", "cmdb_ci_ip_router", sid, net, crit=1,
                 vendor_model=MODELS["router_l" if big else "router_s"], ip=mgmt.take())["name"]
            for i in range(1, w.rand(inv["wan"], r) + 1)]
    fws = [w.ci(f"{lo}-fw-{i:02d}", "cmdb_ci_ip_firewall", sid, net, crit=1,
                vendor_model=MODELS["fw_m" if big else "fw_s"], ip=mgmt.take())["name"]
           for i in range(1, w.rand(inv["fw"], r) + 1)]
    cores = [w.ci(f"{lo}-core-{i:02d}", "cmdb_ci_ip_switch", sid, net, crit=1,
                  vendor_model=MODELS["core"], ip=mgmt.take())["name"]
             for i in range(1, w.rand(inv["core"], r) + 1)]
    for i, f in enumerate(fws):
        w.rel(f, wans[i % len(wans)], "Connects through::Connects")
    for i, c in enumerate(cores):
        w.rel(c, fws[i % len(fws)], "Connects through::Connects")
    acc = []
    for i in range(1, w.rand(inv["access"], r) + 1):
        a = w.ci(f"{lo}-acc-{i:03d}", "cmdb_ci_ip_switch", sid, net, crit=3,
                 vendor_model=MODELS["access"], ip=mgmt.take())["name"]
        w.rel(a, cores[(i - 1) % len(cores)], "Connects through::Connects")
        acc.append(a)
    aps = w.ci(f"{lo}-ap-grp", "cmdb_ci_wap_network", sid, net, crit=3, count=max(20, s["beds"] // 3),
               vendor_model=("Cisco", "Catalyst 9136I", "IOS-XE", "17.9.4a"))
    w.rel(aps["name"], cores[0], "Connects through::Connects")
    medseg = w.ci(f"{lo}-medseg", "cmdb_ci_ip_network", sid, biomed, crit=1, ip="",
                  description=f"Medical-device segment {base}.8.0/21")["name"]
    w.rel(medseg, cores[0], "Connects through::Connects")

    # local storage and servers
    has_array = w.rand(inv["array"], r) == 1 or sid == "H17"
    nservers = w.rand(inv["servers"], r)
    array = esx = None
    if has_array:
        array = w.ci(f"arr-{lo}-01", "cmdb_ci_storage_server", sid, site_it, crit=2,
                     vendor_model=MODELS["array_l"], ip=srv.take(), raid_level="RAID6",
                     capacity_tb=60 if tier == "L" else 30, encrypted=True)["name"]
        w.rel(array, cores[0], "Connects through::Connects")
        esx = []
        for i in range(1, (3 if tier == "L" else 2) + 1):
            h = w.ci(f"{lo}-esx-{i:02d}", "cmdb_ci_esx_server", sid, site_it, crit=2,
                     vendor_model=MODELS["esx"], ip=srv.take())["name"]
            w.rel(h, array, "Stores on::Stores")
            w.rel(h, cores[i % len(cores)], "Connects through::Connects")
            esx.append(h)
    roles_order = {"L": LARGE_ROLES, "C": COMMUNITY_ROLES, "A": CAH_ROLES}[tier]
    roles = roles_order[:min(nservers, len(roles_order))]
    extra = nservers - len(roles)
    dup_pool = ["pacs-cache", "fs", "emr-edge", "print"] if tier == "L" else ["print", "fs"]
    server_roles = roles + [dup_pool[i % len(dup_pool)] for i in range(max(0, extra))]
    if tier == "C" and nservers < 2:
        server_roles = roles_order[:2]
    role_servers = {}
    for i, role in enumerate(server_roles, 1):
        if has_array:
            name = f"{lo}-vm-{i:02d}"
            w.ci(name, "cmdb_ci_vmware_instance", sid, site_it, crit=2,
                 vendor_model=MODELS["vm_win" if role != "pacs-cache" else "vm_lin"], ip=srv.take(),
                 role=ROLE_INFO[role][0])
            w.rel(name, esx[(i - 1) % len(esx)], "Runs on::Runs")
        else:
            name = f"{lo}-srv-{i:02d}"
            w.ci(name, "cmdb_ci_win_server", sid, site_it, crit=2, vendor_model=MODELS["phys_win"],
                 ip=srv.take(), role=ROLE_INFO[role][0])
            w.rel(name, cores[(i - 1) % len(cores)], "Connects through::Connects")
        role_servers.setdefault(role, []).append(name)

    owners = {"IMG": w.owners["img"], "LABSYS": w.owners["labs"], "EMR": w.owners["emr"], "SITE": site_it}
    apps = {}
    for role, servers in role_servers.items():
        label, holds_data, owner_key = ROLE_INFO[role]
        aname = f"{lo}-{role}"
        phi = role not in ("print", "nursecall", "rtls")
        c = w.ci(aname, "cmdb_ci_appl", sid, owners[owner_key], crit=1 if role in ("pacs-cache", "labmw", "monitor-gw") else 2,
                 phi=phi, tier=1 if role in ("pacs-cache", "labmw", "emr-edge") else 2,
                 data_holder=holds_data and (role == "fs" or not has_array), safety="high" if role in ("labmw", "monitor-gw") else "low",
                 description=f"{label}, {sid}", size_gb=200)
        c["service_ip"] = w.by_name[servers[0]]["ip_address"]
        for sv in servers:
            w.rel(aname, sv, "Runs on::Runs")
        apps[role] = aname
        w.dep(aname, "sso-app", protocol="kerberos", rate=30 if tier == "L" else 10, profile="24x7", tz=s["tz"])
        if holds_data and has_array and role != "fs":
            dbn = f"{lo}-{role}-db"
            w.db(dbn, sid, w.owners["dba"], servers[0], array, crit=2, tier=1, phi=True,
                 gb=r.randint(200, 2000))
            w.dep(aname, dbn, protocol="tds", port=1433, rate=400 if tier == "L" else 150, profile="24x7", tz=s["tz"])
    # Servers that are not VMs connect through core; VMs reach the network through their host.

    # site-level interfaces
    tz = s["tz"]
    scale = {"L": 1.0, "C": 0.4, "A": 0.06}[tier]
    labmw = apps.get("labmw", "labmw-central")
    _interface(w, f"LAB-RES-{sid}", "ORU^R01", "MLLP", labmw, "lis-app", 300 * scale, "24x7", tz=tz)
    pacs_target = apps.get("pacs-cache", "pacs-app")
    _interface(w, f"DICOM-MOD-{sid}", "DICOM C-STORE", "DICOM", "", pacs_target, 120 * scale, "24x7", tz=tz,
               src_leg=False, owner=w.owners["img"], via="")
    if "pacs-cache" in apps:
        _interface(w, f"DICOM-FWD-{sid}", "DICOM C-STORE", "DICOM", apps["pacs-cache"], "pacs-app",
                   110 * scale, "24x7", tz=tz, owner=w.owners["img"], via="")
    monitor_src = apps.get("monitor-gw")
    if monitor_src:
        _interface(w, f"VITALS-{sid}", "ORU^R01", "MLLP", monitor_src, "emr-app", 2000 * scale, "24x7", tz=tz)
    # Headline route: critical lab results to the ED worklist, staged in lrc-db-01.
    _interface(w, f"ORU-CRIT-{sid}", "ORU^R01", "MLLP", "lis-app", "emr-app",
               {"L": 18, "C": 6, "A": 0.8}[tier], "24x7", tz=tz, staging="lrc-db-01",
               crit=1)
    w.by_name[f"ORU-CRIT-{sid}"]["description"] = f"Critical lab results to the {sid} Emergency department (ED worklist)"
    svc = w.ci(f"svc-{lo}-ed-critical-results", "cmdb_ci_service_business", sid, w.owners["labs"], crit=1,
               dept=depts["ED"], description=f"ED critical lab results, {sid}")
    w.rel(svc["name"], f"ORU-CRIT-{sid}", "Contains::Contained by")
    for role in ("emr-edge", "print", "rtls", "mwl", "nursecall"):
        if role not in apps:
            continue
        a = apps[role]
        if role == "emr-edge":
            w.dep(a, "emr-app", protocol="https", msg_type="BCA report", rate=12, profile="24x7", tz=tz)
        elif role == "print":
            w.dep("emr-app", a, protocol="ipp", port=631, rate=200 * scale, profile="24x7", tz=tz)
        elif role == "rtls":
            w.dep(a, "emr-app", protocol="https", msg_type="FHIR R4", rate=150, profile="24x7", tz=tz)
        elif role == "mwl":
            w.dep(a, "ris-app", protocol="mllp", msg_type="ORM^O01", rate=60 * scale, profile="24x7", tz=tz)
        elif role == "nursecall":
            w.dep(a, "ie-app", protocol="mllp", msg_type="ADT^A01", rate=40, profile="24x7", tz=tz)

    # medical devices
    def dept_for(*prefs):
        for p in prefs:
            if p in depts:
                return depts[p]
        raise AssertionError(f"{sid}: none of {prefs}")

    def device(kind, cls, idx, dept, safety, *, port, rate, target, profile="24x7", bytes_per=0):
        vm_ = r.choice(MODELS[kind]) if isinstance(MODELS[kind], list) else MODELS[kind]
        name = f"{lo}-{kind}-{idx:02d}"
        w.ci(name, cls, sid, biomed, dept=dept, crit=1 if safety == "high" else 2, phi=True,
             vendor_model=vm_, ip=med.take(), patchable=r.random() >= 0.4, safety=safety)
        w.rel(name, medseg, "Connects through::Connects")
        w.dep(name, target, channel="netflow", port=port, rate=rate, profile=profile, tz=tz, bytes_per=bytes_per)
        return name

    mwl = apps.get("mwl")
    for kind, key, prefs, rate, bpm in (("ct", "ct", ("RAD",), 3, 450e6), ("mri", "mri", ("RAD",), 1.2, 300e6),
                                        ("xray", "xray", ("RAD", "ED", "ORTHO"), 3, 20e6),
                                        ("us", "us", ("RAD", "MAT", "CARD", "ED"), 1.5, 30e6)):
        for i in range(1, w.rand(inv[key], r) + 1):
            dept = depts["RAD"] if kind in ("ct", "mri") else dept_for(*r.sample(prefs, len(prefs)))
            d = device(kind, "cmdb_ci_med_diagnostic_imaging", i, dept, "medium", port=104, rate=rate,
                       target=pacs_target, bytes_per=bpm)
            if mwl:
                w.dep(d, mwl, channel="netflow", port=104, rate=rate * 2, profile="24x7", tz=tz, bytes_per=20e3)
    monitor_target = apps.get("monitor-gw", "emr-app")
    for i in range(1, w.rand(inv["mon"], r) + 1):
        dept = dept_for(*r.sample([p for p in ("ICU", "ED", "CARD", "MEDSURG", "NICU") if p in depts], 1))
        device("mon", "cmdb_ci_med_patient_monitoring", i, dept, "high", port=2575, rate=600,
               target=monitor_target, bytes_per=2e3)
    for i in range(1, w.rand(inv["pump"], r) + 1):
        device("pump", "cmdb_ci_med_therapeutic_device", i, depts["PHARM"], "high", port=443, rate=200,
               target="emr-app", bytes_per=4e3)
    for i in range(1, w.rand(inv["lab"], r) + 1):
        device("lab", "cmdb_ci_med_lab_equipment", i, depts["LAB"], "high", port=5000, rate=40,
               target=labmw, bytes_per=3e3)

    _endpoints(w, s, depts, acc, usr, field, r, inv["ws"], inv["prn"])
    w.site_apps = getattr(w, "site_apps", {})
    w.site_apps[sid] = apps
    w.site_arrays = getattr(w, "site_arrays", {})
    if array:
        w.site_arrays[sid] = array


def _endpoints(w, s, depts, access_switches, usr_pool, owner, r, ws_range, prn_range):
    """Workstations and printers: individual CIs at demo sites, else one
    group CI per department with `count`."""
    sid, lo = s["id"], s["id"].lower()
    n_ws = r.randint(*ws_range)
    n_prn = r.randint(*prn_range)
    codes = sorted(depts)
    weights = [3 if c in ("ED", "ICU", "MEDSURG", "LAB", "RAD", "PC", "UC") else 1 for c in codes]
    tot = sum(weights)
    alloc = {c: max(1, round(n_ws * wt / tot)) for c, wt in zip(codes, weights)}
    palloc = {c: max(1, round(n_prn * wt / tot)) for c, wt in zip(codes, weights)}
    w.ws_by_dept = getattr(w, "ws_by_dept", {})
    n_w = n_p = 0
    for j, c in enumerate(codes):
        sw = access_switches[j % len(access_switches)] if access_switches else None
        if s["demo"]:
            names = []
            for _ in range(alloc[c]):
                n_w += 1
                name = f"{lo}-ws-{n_w:05d}"
                w.ci(name, "cmdb_ci_computer", sid, owner, dept=depts[c], crit=3,
                     vendor_model=MODELS["ws"], ip=usr_pool.take())
                if sw:
                    w.rel(name, sw, "Connects through::Connects")
                names.append(name)
            w.ws_by_dept.setdefault(sid, {})[c] = names
            for _ in range(palloc[c]):
                n_p += 1
                name = f"{lo}-prn-{n_p:04d}"
                w.ci(name, "cmdb_ci_printer", sid, owner, dept=depts[c], crit=4,
                     vendor_model=MODELS["prn"], ip=usr_pool.take())
                if sw:
                    w.rel(name, sw, "Connects through::Connects")
        else:
            g = w.ci(f"{lo}-{c}-ws", "u_cmdb_ci_endpoint_group", sid, owner, dept=depts[c], crit=3,
                     count=alloc[c], vendor_model=MODELS["ws"], description="Workstation group")
            p = w.ci(f"{lo}-{c}-prn", "u_cmdb_ci_endpoint_group", sid, owner, dept=depts[c], crit=4,
                     count=palloc[c], vendor_model=MODELS["prn"], description="Printer group")
            if sw:
                w.rel(g["name"], sw, "Connects through::Connects")
                w.rel(p["name"], sw, "Connects through::Connects")
    w.endpoint_counts = getattr(w, "endpoint_counts", {})
    w.endpoint_counts[sid] = sum(alloc.values()) + sum(palloc.values())


# --------------------------------------------------------------------------
# Clinics and non-acute sites
# --------------------------------------------------------------------------

CLINIC_TYPES = [("primary_care", ["PC"], ["LAB", "PEDS", "WH", "BH"], 55),
                ("urgent_care", ["UC"], ["IMG", "LAB"], 12),
                ("specialty", [], ["CARD", "ORTHO", "WH", "DERM", "ONC", "PT", "BH"], 23),
                ("imaging_center", ["IMG"], ["LAB"], 10)]


def _clinic(w, s):
    sid, lo = s["id"], s["id"].lower()
    r = rng_for(w.seed, "site", sid)
    j = int(sid[1:]) - 1
    base = f"172.{16 + j // 256}.{j % 256}"
    mgmt = w.add_subnet(f"{base}.0/26", sid, "mgmt")
    usr = w.add_subnet(f"{base}.128/25", sid, "users", method="aggregate")
    t, must, opt, _ = r.choices(CLINIC_TYPES, [x[3] for x in CLINIC_TYPES])[0]
    s["subtype"] = t
    codes = list(must)
    target = r.randint(1, 3)
    extras = r.sample(opt, len(opt))
    while len(codes) < target and extras:
        codes.append(extras.pop())
    has_xray = r.random() < 0.30  # DATA_SPEC: X-ray at 30% of clinics
    if has_xray and not {"IMG", "UC", "ORTHO"} & set(codes):
        if len(codes) < 3:
            codes.append("IMG")
        else:
            codes[-1] = "IMG"
    depts = {c: w.add_dept(sid, c) for c in codes}
    state = s["state"]
    net = w.group(f"NW-Network-{state}", f"Network operations, {state}")
    biomed = w.group(f"NW-Clinical-Engineering-{state}", f"Clinical engineering (biomed), {state}")
    field = w.group(f"NW-Field-Services-{state}", f"End-user and field services, {state}")
    wan = w.ci(f"{lo}-wan-01", "cmdb_ci_ip_router", sid, net, crit=2, vendor_model=MODELS["router_s"],
               ip=mgmt.take())["name"]
    fw = w.ci(f"{lo}-fw-01", "cmdb_ci_ip_firewall", sid, net, crit=2, vendor_model=MODELS["fw_s"],
              ip=mgmt.take())["name"]
    core = w.ci(f"{lo}-core-01", "cmdb_ci_ip_switch", sid, net, crit=2, vendor_model=MODELS["access_s"],
                ip=mgmt.take())["name"]
    w.rel(fw, wan, "Connects through::Connects")
    w.rel(core, fw, "Connects through::Connects")
    acc = []
    for i in range(1, r.randint(1, 2) + 1):
        a = w.ci(f"{lo}-acc-{i:02d}", "cmdb_ci_ip_switch", sid, net, crit=3, vendor_model=MODELS["access_s"],
                 ip=mgmt.take())["name"]
        w.rel(a, core, "Connects through::Connects")
        acc.append(a)
    ap = w.ci(f"{lo}-ap-grp", "cmdb_ci_wap_network", sid, net, crit=3, count=r.randint(2, 6),
              vendor_model=("Cisco", "Catalyst 9120AXI", "IOS-XE", "17.9.4a"))["name"]
    w.rel(ap, core, "Connects through::Connects")
    # medical devices (no servers at clinics: devices talk to central systems)
    devices = []
    img_dept = next((depts[c] for c in ("IMG", "UC", "ORTHO") if c in depts), None)
    if has_xray:
        devices.append(("xray", "cmdb_ci_med_diagnostic_imaging", img_dept, "pacs-app", 104, 1.5, 20e6))
    us_dept = next((depts[c] for c in ("IMG", "WH", "CARD") if c in depts), None)
    if us_dept and r.random() < 0.6:
        devices.append(("us", "cmdb_ci_med_diagnostic_imaging", us_dept, "pacs-app", 104, 1, 30e6))
    if "LAB" in depts:
        for _ in range(r.randint(0, 2)):
            devices.append(("lab", "cmdb_ci_med_lab_equipment", depts["LAB"], "labmw-central", 5000, 10, 3e3))
    if devices:
        med = w.add_subnet(f"{base}.64/27", sid, "medical", method="passive")
        seg = w.ci(f"{lo}-medseg", "cmdb_ci_ip_network", sid, biomed, crit=2,
                   description=f"Medical-device segment {base}.64/27")["name"]
        w.rel(seg, core, "Connects through::Connects")
        counter = {}
        for kind, cls, dept, target, port, rate, bpm in devices:
            counter[kind] = counter.get(kind, 0) + 1
            name = f"{lo}-{kind}-{counter[kind]:02d}"
            vm_ = r.choice(MODELS[kind])
            w.ci(name, cls, sid, biomed, dept=dept, crit=2, phi=True, vendor_model=vm_, ip=med.take(),
                 patchable=r.random() >= 0.4, safety="medium" if kind != "lab" else "high")
            w.rel(name, seg, "Connects through::Connects")
            w.dep(name, target, channel="netflow", port=port, rate=rate, profile="clinic", tz=s["tz"],
                  bytes_per=bpm)
    _endpoints(w, s, depts, acc, usr, field, r, (10, 30), (2, 5))


def _nonacute(w, s):
    sid, lo = s["id"], s["id"].lower()
    r = rng_for(w.seed, "site", sid)
    j = int(sid[1:]) - 1
    base = f"172.{20 + j // 256}.{j % 256}"
    mgmt = w.add_subnet(f"{base}.0/26", sid, "mgmt")
    usr = w.add_subnet(f"{base}.128/25", sid, "users", method="aggregate")
    code = next(c for t, c, _ in NONACUTE_TYPES if t == s["subtype"])
    depts = {code: w.add_dept(sid, code)}
    state = s["state"]
    net = w.group(f"NW-Network-{state}", f"Network operations, {state}")
    field = w.group(f"NW-Field-Services-{state}", f"End-user and field services, {state}")
    lte = s["subtype"] in ("home_health", "hospice") and r.random() < 0.5
    wan = w.ci(f"{lo}-wan-01", "cmdb_ci_ip_router", sid, net, crit=3,
               vendor_model=MODELS["lte" if lte else "router_s"], ip=mgmt.take())["name"]
    up = wan
    if r.random() < 0.6:
        fw = w.ci(f"{lo}-fw-01", "cmdb_ci_ip_firewall", sid, net, crit=3, vendor_model=MODELS["fw_s"],
                  ip=mgmt.take())["name"]
        w.rel(fw, wan, "Connects through::Connects")
        up = fw
    acc = []
    if r.random() < 0.5:
        a = w.ci(f"{lo}-acc-01", "cmdb_ci_ip_switch", sid, net, crit=4, vendor_model=MODELS["access_s"],
                 ip=mgmt.take())["name"]
        w.rel(a, up, "Connects through::Connects")
        acc.append(a)
    ap = w.ci(f"{lo}-ap-grp", "cmdb_ci_wap_network", sid, net, crit=4, count=r.randint(1, 4),
              vendor_model=("Cisco", "Catalyst 9105AXI", "IOS-XE", "17.9.4a"))["name"]
    w.rel(ap, acc[0] if acc else up, "Connects through::Connects")
    _endpoints(w, s, depts, acc or [ap], usr, field, r, (3, 15), (1, 3))


def _gcc_site(w):
    """Hyderabad capability centre: network, laptops (individual CIs)."""
    sid = "GCC-HYD"
    s = w.site_by_id[sid]
    r = rng_for(w.seed, "site", sid)
    mgmt = w.add_subnet("10.50.0.0/24", sid, "mgmt")
    usr = w.add_subnet("10.50.16.0/20", sid, "users")
    depts = {c: w.add_dept(sid, c) for c in ("CLOUD", "CLINAPPS", "INTEG", "CYBER", "DBA")}
    net = w.group("HYD-Network", "GCC Hyderabad network")
    field = w.group("HYD-Workplace-IT", "GCC Hyderabad workplace IT")
    wans = [w.ci(f"hyd-wan-0{i}", "cmdb_ci_ip_router", sid, net, crit=2, vendor_model=MODELS["router_l"],
                 ip=mgmt.take())["name"] for i in (1, 2)]
    fws = [w.ci(f"hyd-fw-0{i}", "cmdb_ci_ip_firewall", sid, net, crit=2, vendor_model=MODELS["fw_m"],
                ip=mgmt.take())["name"] for i in (1, 2)]
    cores = [w.ci(f"hyd-core-0{i}", "cmdb_ci_ip_switch", sid, net, crit=2, vendor_model=MODELS["core"],
                  ip=mgmt.take())["name"] for i in (1, 2)]
    for i in range(2):
        w.rel(fws[i], wans[i], "Connects through::Connects")
        w.rel(cores[i], fws[i], "Connects through::Connects")
    acc = []
    for i in range(1, 11):
        a = w.ci(f"hyd-acc-{i:02d}", "cmdb_ci_ip_switch", sid, net, crit=3, vendor_model=MODELS["access"],
                 ip=mgmt.take())["name"]
        w.rel(a, cores[i % 2], "Connects through::Connects")
        acc.append(a)
    ap = w.ci("hyd-ap-grp", "cmdb_ci_wap_network", sid, net, crit=3, count=40,
              vendor_model=("Cisco", "Catalyst 9136I", "IOS-XE", "17.9.4a"))["name"]
    w.rel(ap, cores[0], "Connects through::Connects")
    sizes = {"CLINAPPS": 90, "CLOUD": 70, "INTEG": 60, "CYBER": 50, "DBA": 30}
    w.ws_by_dept = getattr(w, "ws_by_dept", {})
    k = 0
    for j, c in enumerate(sorted(sizes)):
        names = []
        for _ in range(sizes[c]):
            k += 1
            name = f"hyd-lt-{k:04d}"
            w.ci(name, "cmdb_ci_computer", sid, field, dept=depts[c], crit=3, vendor_model=MODELS["laptop"],
                 ip=usr.take(), residency="IN")
            w.rel(name, acc[j % len(acc)], "Connects through::Connects")
            names.append(name)
        w.ws_by_dept.setdefault(sid, {})[c] = names
    w.gcc_sizes = sizes


# --------------------------------------------------------------------------
# Identity (DATA_SPEC section 5)
# --------------------------------------------------------------------------

JOB_GRANTS = {  # job -> [(target, level)]
    "physician": [("emr-app", "write"), ("pacs-app", "read"), ("lis-app", "read")],
    "nurse": [("emr-app", "write"), ("pharm-app", "read")],
    "unit-clerk": [("emr-app", "read"), ("sched-app", "write")],
    "radiologist": [("pacs-app", "write"), ("ris-app", "write"), ("emr-app", "read")],
    "rad-tech": [("ris-app", "write"), ("pacs-app", "write")],
    "lab-tech": [("lis-app", "write"), ("emr-app", "read")],
    "pathologist": [("lis-app", "write"), ("emr-app", "read")],
    "pharmacist": [("pharm-app", "write"), ("emr-app", "read"), ("lis-app", "read")],
    "pharmacy-tech": [("pharm-app", "write")],
    "registrar": [("sched-app", "write"), ("emr-app", "write")],
    "coder": [("billing-app", "write"), ("emr-app", "read")],
    "therapist": [("emr-app", "write")],
    "biomed-tech": [("emr-app", "read")],
    "admin-clerk": [("ext-erp-hr-saas", "write"), ("ext-email-saas", "write")],
    "caregiver": [("emr-app", "read")],
    "cloud-ops": [("portal-app", "admin"), ("dwh-etl-app", "admin"), ("mon-app", "admin"),
                  ("gcc-automation-app", "admin")],
    "clinical-apps-support": [("emr-app", "write"), ("emr-db-01", "admin")],
    "integration-eng": [("ie-app", "admin"), ("ie-db-01", "read")],
    "secops": [("siem-app", "admin"), ("sso-app", "read")],
    "dba": [],  # granted admin on every production database below
}
ENTERPRISE_GROUPS = {"CLOUD": "grp-hyd-cloud-ops", "CLINAPPS": "grp-hyd-clinical-apps-support",
                     "INTEG": "grp-integration-eng", "CYBER": "grp-secops", "DBA": "grp-dba"}


def _identity(w):
    r = rng_for(w.seed, "identity")
    roles = [{"id": f"role-{j}", "name": j.replace("-", " ").title()} for j in sorted(JOB_GRANTS)]
    grants = []
    dbs = sorted(c["name"] for c in w.cis if c["label"] == "Database" and c["environment"] == "production")
    for j, targets in sorted(JOB_GRANTS.items()):
        for t, lvl in targets:
            grants.append({"role": f"role-{j}", "target": t, "level": lvl})
    for d in dbs:
        grants.append({"role": "role-dba", "target": d, "level": "admin"})
    groups, users, memberships = [], [], []
    ws = getattr(w, "ws_by_dept", {})
    for d in w.departments:
        site = w.site_by_id[d["site"]]
        if site["id"] == "GCC-HYD":
            continue
        jobs = DEPT_JOBS.get(d["code"], DEFAULT_JOBS)
        for j in jobs:
            gid = f"grp-{d['site']}-{d['code']}-{j}"
            if site["demo"]:
                n_ws = len(ws[d["site"]][d["code"]])
                n = max(1, round(n_ws * r.uniform(3.0, 4.0) / len(jobs)))
            else:
                n = {"large_hospital": r.randint(20, 120), "community_hospital": r.randint(8, 50),
                     "critical_access_hospital": r.randint(2, 12), "clinic": r.randint(2, 12),
                     "non_acute": r.randint(2, 15)}[site["type"]]
            groups.append({"id": gid, "site": d["site"], "department": d["id"], "job_role": j,
                           "roles": [f"role-{j}"], "member_count": n, "scope": "site"})
            if site["demo"]:
                for _ in range(n):
                    users.append(_user(w, r, d["site"], d["id"], j, gid, ws[d["site"]][d["code"]]))
                    memberships.append({"user": users[-1]["id"], "group": gid})
    for code, gid in sorted(ENTERPRISE_GROUPS.items()):
        j = DEPT_JOBS[code][0]
        n = w.gcc_sizes[code]
        groups.append({"id": gid, "site": "GCC-HYD", "department": f"GCC-HYD-{code}", "job_role": j,
                       "roles": [f"role-{j}"], "member_count": n, "scope": "enterprise"})
        for _ in range(n):
            users.append(_user(w, r, "GCC-HYD", f"GCC-HYD-{code}", j, gid, ws["GCC-HYD"][code]))
            memberships.append({"user": users[-1]["id"], "group": gid})
    w.identity = {"users": users, "groups": groups, "roles": roles, "grants": grants,
                  "memberships": memberships}


def _user(w, r, site, dept, job, gid, workstations):
    key = "hyd" if site == "GCC-HYD" else site.lower()
    w.user_seq = getattr(w, "user_seq", {})
    w.user_seq[key] = w.user_seq.get(key, 0) + 1
    return {"id": f"u-{key}-{w.user_seq[key]:05d}", "site": site, "department": dept, "job_role": job,
            "status": "disabled" if r.random() < 0.03 else "active", "status_source": "ext-erp-hr-saas",
            "primary_group": gid, "workstation": r.choice(workstations)}


# --------------------------------------------------------------------------
# Change requests (approved windows gate admin access to production DBs)
# --------------------------------------------------------------------------


def _changes(w):
    r = rng_for(w.seed, "changes")
    from datetime import datetime, timedelta, timezone
    end = datetime(2026, 10, 14, 23, 59, 59, tzinfo=timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS) + timedelta(seconds=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    targets = ["emr-db-01", "emr-db-01", "emr-db-01", "lis-db-01", "pharm-db-01", "ris-db-01",
               "billing-db-01", "sched-db-01", "ie-db-01", "pacs-db-01", "emr-db-01", "lis-db-01"]
    changes = []
    for i, t in enumerate(targets):
        day = start + timedelta(days=1 + i)
        s = day.replace(hour=9, minute=30)  # 02:30 Pacific: overnight maintenance window
        changes.append({"number": f"CHG00{31200 + i * 7}", "short_description": f"Index maintenance / patching on {t}",
                        "type": "normal", "state": "closed", "approval": "approved", "cmdb_ci": t,
                        "assignment_group": "NW-DBA", "start_date": s.strftime(fmt),
                        "end_date": (s + timedelta(hours=3)).strftime(fmt), "risk": "moderate"})
    for i, t in enumerate(["dc1-host-03", "h05-wan-01", "pacs-app", "sso-app", "c0412-fw-01", "arr-pri-b-02"]):
        s = start + timedelta(days=r.randint(0, 12), hours=9)
        changes.append({"number": f"CHG00{31400 + i * 11}", "short_description": f"Routine maintenance on {t}",
                        "type": "standard", "state": "closed", "approval": "approved", "cmdb_ci": t,
                        "assignment_group": w.by_name[t]["support_group"], "start_date": s.strftime(fmt),
                        "end_date": (s + timedelta(hours=2)).strftime(fmt), "risk": "low"})
    for i, t in enumerate(["dc1-host-11", "emr-app", "h17-core-01"]):
        s = end + timedelta(days=2 + i, hours=9, seconds=1)
        changes.append({"number": f"CHG00{31500 + i * 13}", "short_description": f"Planned upgrade on {t}",
                        "type": "normal", "state": "scheduled", "approval": "approved", "cmdb_ci": t,
                        "assignment_group": w.by_name[t]["support_group"], "start_date": s.strftime(fmt),
                        "end_date": (s + timedelta(hours=4)).strftime(fmt), "risk": "moderate"})
    s = end + timedelta(days=2, hours=10, seconds=1)  # 2026-10-17 10:00Z = 03:00 Pacific
    changes.append({"number": "CHG-HEADLINE",
                    "short_description": "Apply ESXi security patch and reboot dc1-host-07",
                    "type": "normal", "state": "scheduled", "approval": "requested", "cmdb_ci": "dc1-host-07",
                    "assignment_group": "NW-Datacenter-Infra", "start_date": s.strftime(fmt),
                    "end_date": (s + timedelta(hours=2)).strftime(fmt), "risk": "low"})
    w.changes = sorted(changes, key=lambda c: c["number"])
