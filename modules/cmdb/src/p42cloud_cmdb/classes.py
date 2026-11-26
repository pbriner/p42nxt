"""The CMDB's tables: one per class of configuration item (CI), each with the common CI fields
and its own; the index of all CIs; relation types and relationships; interfaces, ports and
cables; what scans find and the review list."""

from dataclasses import dataclass
from typing import Any

from p42cloud.sdk import (
    AnyRecord,
    Boolean,
    Choice,
    Currency,
    Date,
    DateTime,
    Decimal,
    Integer,
    Json,
    Money,
    Number,
    Reference,
    String,
    Table,
    Text,
)

STATUSES = {
    "planned": "Planned",
    "ordered": "Ordered",
    "in_use": "In use",
    "in_repair": "In repair",
    "in_stock": "In stock",
    "retired": "Retired",
}
TONES = {"in_use": "primary", "in_repair": "error", "retired": "muted"}
ENVIRONMENTS = {"production": "Production", "test": "Test", "development": "Development"}
LEVELS = {"high": "High", "medium": "Medium", "low": "Low"}


def common() -> dict[str, Any]:
    """The fields every class has, in this order on its form."""
    return {
        "name": String("Name", max_length=160, required=True),
        "status": Choice("Status", STATUSES, required=True, default="in_use", tones=TONES),
        "environment": Choice("Environment", ENVIRONMENTS, default="production"),
        "criticality": Choice("Criticality", LEVELS, default="medium"),
        "owner": Reference("Owner", "sys.person"),
        "support_group": Reference("Support group", "sys.group"),
        "place": Reference("Place", "places.place"),
        "manufacturer": Reference("Manufacturer", "partners.partner"),
        "model": String("Model", max_length=120),
        "serial": String("Serial number", max_length=120),
        "asset_tag": String("Asset tag", max_length=60),
        "supplier": Reference("Supplier", "partners.partner"),
        "bought_on": Date("Bought on"),
        "currency": Currency("Currency", default="CHF"),
        "price": Money("Price", currency_field="currency"),
        "warranty_until": Date("Warranty until"),
        "notes": Text("Notes"),
        # How a scan or the installation finds it again; unique among CIs of the class.
        "discovery_key": String("Discovery key", max_length=200, read_only=True),
        "last_seen": DateTime("Last seen", read_only=True),
        # Which fields come from scans (the rest is typed and never overwritten by one).
        "scanned": Json("From scans", read_only=True),
    }


COMMON = tuple(common())
RACKED: dict[str, Any] = {
    "rack": Reference("Rack", "places.place"),
    "rack_unit": Integer("From unit", help="The lowest unit it takes in the rack."),
    "height_units": Integer("Height (units)", default=1),
    "rack_side": Choice("Side", {"front": "Front", "back": "Back"}, default="front"),
}


@dataclass(frozen=True)
class CiClass:
    """A class shipped with the module: its table, family and own fields."""

    key: str
    label: str
    plural: str
    family: str
    icon: str
    own: dict[str, Any]
    kinds: dict[str, str] | None = None


def _computer_fields() -> dict[str, Any]:
    return {
        "host_name": String("Host name", max_length=253),
        "os": String("Operating system", max_length=80),
        "os_version": String("OS version", max_length=60),
        "cpu": String("CPU", max_length=120),
        "ram_gb": Integer("RAM (GB)"),
        "disk_gb": Integer("Disk (GB)"),
    }


CLASSES: tuple[CiClass, ...] = (
    CiClass(
        "computer",
        "Computer",
        "Computers",
        "hardware",
        "computer",
        _computer_fields(),
        {
            "laptop": "Laptop",
            "desktop": "Desktop",
            "workstation": "Workstation",
            "thin": "Thin client",
        },
    ),
    CiClass(
        "server",
        "Server",
        "Servers",
        "hardware",
        "dns",
        _computer_fields()
        | RACKED
        | {
            "power_supplies": Integer("Power supplies"),
            "oob_address": String("Out-of-band address", max_length=253),
            "hypervisor": String(
                "Hypervisor", max_length=60, help="Proxmox, ESXi … if it hosts VMs."
            ),
        },
    ),
    CiClass(
        "network_device",
        "Network device",
        "Network devices",
        "hardware",
        "router",
        RACKED
        | {
            "firmware": String("Firmware", max_length=80),
            "management_address": String("Management address", max_length=253),
            "port_count": Integer("Ports"),
            "poe": Boolean("PoE", default=False),
            "ssids": Text("SSIDs", help="Access points: one per line."),
        },
        {
            "router": "Router",
            "switch": "Switch",
            "firewall": "Firewall",
            "access_point": "Access point",
            "modem": "Modem",
            "load_balancer": "Load balancer",
            "gateway": "Gateway",
        },
    ),
    CiClass(
        "mobile_device",
        "Mobile device",
        "Mobile devices",
        "hardware",
        "smartphone",
        {
            "imei": String("IMEI", max_length=20),
            "phone_number": String("Phone number", max_length=40),
            "os": String("Operating system", max_length=80),
        },
        {"phone": "Phone", "tablet": "Tablet"},
    ),
    CiClass(
        "storage",
        "Storage",
        "Storage",
        "hardware",
        "storage",
        RACKED
        | {
            "capacity_tb": Integer("Capacity (TB)"),
            "used_tb": Integer("Used (TB)"),
            "raid": String("RAID level", max_length=20),
        },
        {"nas": "NAS", "san": "SAN", "disk": "External disk"},
    ),
    CiClass(
        "printer",
        "Printer",
        "Printers",
        "hardware",
        "print",
        {"address": String("Address", max_length=253)},
        {"printer": "Printer", "scanner": "Scanner", "multifunction": "Multifunction"},
    ),
    CiClass(
        "ip_phone",
        "IP phone",
        "IP phones",
        "hardware",
        "call",
        {"extension": String("Extension", max_length=20)},
    ),
    CiClass(
        "power_device",
        "Power device",
        "Power devices",
        "hardware",
        "battery_charging_full",
        RACKED
        | {
            "capacity_va": Integer("Capacity (VA)"),
            "battery_date": Date("Battery from"),
        },
        {"ups": "UPS", "pdu": "PDU"},
    ),
    CiClass(
        "iot_device",
        "IoT device",
        "IoT devices",
        "hardware",
        "sensors",
        {"address": String("Address", max_length=253)},
        {
            "camera": "Camera",
            "sensor": "Sensor",
            "smart_home": "Smart home",
            "media": "TV and media",
        },
    ),
    CiClass(
        "shelly_device",
        "Shelly device",
        "Shelly devices",
        "hardware",
        "power",
        {
            "device_id": String("Device ID", max_length=60),
            "generation": Integer("Generation"),
            "firmware": String("Firmware", max_length=80),
            "address": String("Local address", max_length=64),
            # In another network: the agent there reads and switches it (its ID).
            "agent": Reference("Through agent", "sys.agent"),
            "channels": Integer("Channels", default=1),
            "online": Boolean("Online", read_only=True, default=False),
            "switched_on": Boolean("Switched on", read_only=True),
            "power_w": Decimal("Power (W)", precision=10, scale=1, read_only=True),
            "energy_kwh": Decimal("Energy (kWh)", precision=14, scale=3, read_only=True),
            "temperature": Decimal("Temperature (°C)", precision=6, scale=1, read_only=True),
            "state_read": DateTime("State read", read_only=True),
        },
        {
            "plug": "Plug",
            "relay": "Relay",
            "dimmer": "Dimmer",
            "meter": "Energy meter",
            "sensor": "Sensor",
        },
    ),
    CiClass(
        "vm",
        "Virtual machine",
        "Virtual machines",
        "virtual",
        "cloud",
        {
            "host_name": String("Host name", max_length=253),
            "os": String("Operating system", max_length=80),
            "vcpu": Integer("vCPU"),
            "ram_gb": Integer("RAM (GB)"),
            "disk_gb": Integer("Disk (GB)"),
            "vm_id": String("ID on its host", max_length=60),
        },
        {"vm": "Virtual machine", "lxc": "Container (LXC)"},
    ),
    CiClass(
        "container_engine",
        "Container engine",
        "Container engines",
        "containers",
        "deployed_code",
        {
            "version": String("Version", max_length=40),
            "api_address": String("API address", max_length=253),
        },
        {"docker": "Docker", "podman": "Podman"},
    ),
    CiClass(
        "container_stack",
        "Stack",
        "Stacks",
        "containers",
        "stacks",
        {"project": String("Project", max_length=120), "compose_file": Text("Compose file")},
    ),
    CiClass(
        "container",
        "Container",
        "Containers",
        "containers",
        "deployed_code",
        {
            "image": String("Image", max_length=255),
            "tag": String("Tag", max_length=128),
            "digest": String("Digest", max_length=80),
            "state": String("State", max_length=40),
            "ports": Text("Published ports"),
            "restart_policy": String("Restart policy", max_length=40),
        },
    ),
    CiClass(
        "container_image",
        "Image",
        "Images",
        "containers",
        "photo_library",
        {
            "repository": String("Repository", max_length=255),
            "tag": String("Tag", max_length=128),
            "digest": String("Digest", max_length=80),
        },
    ),
    CiClass(
        "container_network",
        "Container network",
        "Container networks",
        "containers",
        "lan",
        {"driver": String("Driver", max_length=40), "subnet": String("Subnet", max_length=64)},
    ),
    CiClass(
        "container_volume",
        "Volume",
        "Volumes",
        "containers",
        "folder",
        {"driver": String("Driver", max_length=40), "size_gb": Integer("Size (GB)")},
    ),
    CiClass(
        "db_instance",
        "Database server",
        "Database servers",
        "data",
        "database",
        {
            "engine": String("Engine", max_length=40),
            "version": String("Version", max_length=60),
            "port": Integer("Port"),
            "size_gb": Integer("Size (GB)"),
        },
    ),
    CiClass(
        "database",
        "Database",
        "Databases",
        "data",
        "table",
        {"size_gb": Integer("Size (GB)"), "backup": String("Backups", max_length=120)},
    ),
    CiClass(
        "application",
        "Application",
        "Applications",
        "software",
        "apps",
        {
            "version": String("Version", max_length=60),
            "url": String("Address", max_length=500),
            "vendor": String("Vendor", max_length=120),
        },
    ),
    CiClass(
        "business_application",
        "Business application",
        "Business applications",
        "software",
        "business_center",
        {
            "business_owner": Reference("Business owner", "sys.person"),
            "users": Integer("Users"),
            "classification": Choice(
                "Data classification",
                {"public": "Public", "internal": "Internal", "confidential": "Confidential"},
                default="internal",
            ),
            "url": String("Address", max_length=500),
        },
    ),
    CiClass(
        "certificate",
        "Certificate",
        "Certificates",
        "security",
        "verified_user",
        {
            "common_name": String("Common name", max_length=253),
            "alt_names": Text("Other names"),
            "issuer": String("Issuer", max_length=255),
            "cert_serial": String("Certificate serial", max_length=80),
            "valid_from": Date("Valid from"),
            "valid_until": Date("Valid until"),
            "key_type": String("Key", max_length=40),
            "fingerprint": String("Fingerprint (SHA-256)", max_length=100),
            "auto_renewed": Boolean("Renewed by itself (ACME)", default=False),
            "found_at": String("Found at", max_length=255, help="host:port it was read from."),
            "pem": Text("Certificate (PEM)", help="Paste it: the fields are read from it."),
            "warned": Integer("Warned (days before)", read_only=True),
        },
    ),
    CiClass(
        "patch_panel",
        "Patch panel",
        "Patch panels",
        "hardware",
        "settings_input_hdmi",
        RACKED | {"port_count": Integer("Ports")},
    ),
    CiClass(
        "agent",
        "p42 agent",
        "p42 agents",
        "platform",
        "smart_toy",
        {
            "version": String("Version", max_length=40),
            "enrolled": DateTime("Enrolled", read_only=True),
            "covers": Text("Covers", help="The subnets and tasks it works on."),
            "reported_host": String("Host it reports", max_length=253),
        },
    ),
)
BY_KEY = {c.key: c for c in CLASSES}
FAMILIES = {
    "hardware": "Hardware",
    "virtual": "Virtual",
    "containers": "Containers",
    "data": "Data",
    "software": "Software",
    "security": "Security",
    "platform": "Platform",
}


def class_table(c: CiClass) -> Table:
    fields = common()
    if c.kinds:
        fields["kind"] = Choice("Kind", c.kinds, required=True, default=next(iter(c.kinds)))
    fields |= c.own
    return Table(c.key, c.label, fields, label_plural=c.plural, display="name", work_notes=True)


CI = Table(
    "ci",
    "Configuration item",
    {
        "number": Number("Number", prefix="CI"),
        "name": String("Name", max_length=160, required=True),
        "ci_class": String("Class", max_length=120, required=True),
        "family": String("Family", max_length=40),
        "table": String("Table", max_length=120, required=True),
        "record": AnyRecord("Record", table_field="table", required=True),
        "status": Choice("Status", STATUSES, tones=TONES),
        "criticality": Choice("Criticality", LEVELS),
        "owner": Reference("Owner", "sys.person"),
        "place": Reference("Place", "places.place"),
        "last_seen": DateTime("Last seen"),
    },
    label_plural="All configuration items",
    display="name",
    unique_together=(("table", "record"),),
)

CI_CLASS = Table(
    "ci_class",
    "CI class",
    {
        "name": String("Key", max_length=120, required=True, unique=True),
        "label": String("Name", max_length=120, required=True),
        "table": String("Table", max_length=120, required=True, unique=True),
        "family": String("Family", max_length=40),
        "icon": String("Icon", max_length=60),
        "custom": Boolean("Made by an administrator", required=True, default=False),
    },
    label_plural="CI classes",
    display="label",
)

RELATION_TYPE = Table(
    "relation_type",
    "Relation type",
    {
        "name": String("Key", max_length=60, required=True, unique=True),
        "label": String("Label", max_length=80, required=True),
        "reverse_label": String("Read the other way", max_length=80, required=True),
        "icon": String("Icon", max_length=60),
        "accent": Integer("Colour (1 to 8)"),
        # Class keys it may link; empty: any.
        "from_classes": Json("From classes"),
        "to_classes": Json("To classes"),
        "meaning": Choice(
            "On maps",
            {"needs": "One needs the other", "link": "Only a link"},
            required=True,
            default="needs",
        ),
        "both_ways": Boolean("The same both ways", required=True, default=False),
        "custom": Boolean("Made by a manager", required=True, default=False),
        "changed": Boolean("Changed by a manager", default=False, read_only=True),
    },
    label_plural="Relation types",
    display="label",
)

RELATIONSHIP = Table(
    "relationship",
    "Relationship",
    {
        "from_ci": Reference("From", "cmdb.ci", required=True, on_delete="cascade"),
        "type": Reference("Type", "cmdb.relation_type", required=True, on_delete="restrict"),
        "to_ci": Reference("To", "cmdb.ci", required=True, on_delete="cascade"),
        "origin": Choice(
            "Source",
            {"typed": "Typed", "scanned": "Scanned", "registered": "Registered"},
            required=True,
            default="typed",
        ),
        "notes": Text("Notes"),
    },
    label_plural="Relationships",
    unique_together=(("from_ci", "type", "to_ci"),),
)

INTERFACE = Table(
    "interface",
    "Network interface",
    {
        "ci": Reference("Device", "cmdb.ci", required=True, on_delete="cascade"),
        "name": String("Name", max_length=60, required=True),
        "mac": String("MAC address", max_length=17, unique=True),
        "speed": String("Speed", max_length=20),
        "vlan": Integer("VLAN"),
    },
    label_plural="Network interfaces",
    display="name",
)

PORT = Table(
    "port",
    "Port",
    {
        "ci": Reference("On", "cmdb.ci", required=True, on_delete="cascade"),
        "name": String("Name", max_length=40, required=True),
        "kind": Choice(
            "Kind",
            {"rj45": "RJ45", "sfp": "SFP", "fibre": "Fibre", "power": "Power", "other": "Other"},
            required=True,
            default="rj45",
        ),
        "label": String("Label", max_length=60),
    },
    label_plural="Ports",
    display="name",
    unique_together=(("ci", "name"),),
)

CABLE = Table(
    "cable",
    "Cable",
    {
        "from_port": Reference("From port", "cmdb.port", required=True, on_delete="cascade"),
        "to_port": Reference("To port", "cmdb.port", required=True, on_delete="cascade"),
        "kind": Choice(
            "Kind",
            {
                "cat6": "Cat6",
                "cat6a": "Cat6a",
                "fibre": "Fibre",
                "power": "Power",
                "other": "Other",
            },
            default="cat6",
        ),
        "label": String("Label", max_length=60),
        "length_m": Integer("Length (m)"),
        "colour": String("Colour", max_length=30),
    },
    label_plural="Cables",
    display="label",
)

REVIEW = Table(
    "review",
    "Found, to review",
    {
        "found": String("What was found", max_length=255, required=True),
        "found_by": String("Found by", max_length=60, required=True),
        "ci_class": String("Class name", max_length=120),
        # Picked from the classes in forms and lists; the name above follows it.
        "ci_class_ref": Reference("Proposed class", "cmdb.ci_class", key="ci_class"),
        "facts": Json("Facts", required=True),
        # From the facts, as text, so lists show and search them.
        "address": String("IP address", max_length=64),
        "mac": String("MAC address", max_length=32),
        "host_name": String("Host name", max_length=253),
        "match_key": String("Key", max_length=200, required=True),
        "state": Choice(
            "State",
            {
                "waiting": "Waiting",
                "created": "Created",
                "attached": "Attached",
                "ignored": "Ignored",
            },
            required=True,
            default="waiting",
            tones={"waiting": "primary", "ignored": "muted"},
            read_only=True,  # changed by the review's buttons, which do the work
        ),
        "ci": Reference("Configuration item", "cmdb.ci"),
        "seen": DateTime("Last seen", required=True),
        "times": Integer("Times seen", required=True, default=1),
    },
    label_plural="Review list",
    display="found",
)

TABLES: tuple[Table, ...] = (
    CI,
    CI_CLASS,
    RELATION_TYPE,
    RELATIONSHIP,
    INTERFACE,
    PORT,
    CABLE,
    REVIEW,
    *(class_table(c) for c in CLASSES),
)
