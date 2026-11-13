"""Groups, roles and grants: who (groups) may do what (roles, made of grants).

Roles come only through groups: a person is a member of groups, groups hold roles, roles hold
grants and may include other roles.
"""

from p42cloud.data.declarations import Table
from p42cloud.data.types.boolean import Boolean
from p42cloud.data.types.choice import Choice
from p42cloud.data.types.json import Json
from p42cloud.data.types.reference import Reference
from p42cloud.data.types.string import String
from p42cloud.data.types.text import Text
from p42cloud.data.types.translated_text import TranslatedText

OPERATIONS = {
    "read": "Read",
    "create": "Create",
    "write": "Change",
    "delete": "Delete",
    "restore": "Restore",
}

GROUP = Table(
    "group",
    "Group",
    {
        "name": String("Name", max_length=80, required=True, unique=True),
        "description": Text("Description"),
        "assignable": Boolean("Work can be assigned to it", required=True, default=False),
        # A group with a rule gets its members from it: every active person it matches.
        "rule": Json("Members are the people where"),
    },
    label_plural="Groups",
)

MEMBERSHIP = Table(
    "membership",
    "Membership",
    {
        "group": Reference("Group", "sys.group", required=True),
        "person": Reference("Person", "sys.person", required=True),
        # Kept by the group's rule, not by hand.
        "from_rule": Boolean("From the group's rule", required=True, default=False),
    },
    label_plural="Memberships",
    unique_together=(("group", "person"),),
)

ROLE = Table(
    "role",
    "Role",
    {
        "name": String("Name", max_length=80, required=True, unique=True),
        "label": TranslatedText("Label", required=True),
        "description": TranslatedText("Description"),
        "module": String("Module", max_length=16, required=True),
        # Built by access administrators in the app, not declared by a module.
        "custom": Boolean("Custom", required=True, default=False),
        # Only members of a group holding sys.admin may give these to a group.
        "powerful": Boolean("Powerful", required=True, default=False),
    },
    label_plural="Roles",
)

ROLE_INCLUDE = Table(
    "role_include",
    "Included role",
    {
        "role": Reference("Role", "sys.role", required=True),
        "includes": Reference("Includes", "sys.role", required=True),
    },
    label_plural="Included roles",
    unique_together=(("role", "includes"),),
)

GRANT = Table(
    "grant",
    "Grant",
    {
        "role": Reference("Role", "sys.role", required=True),
        "table": Reference("Table", "sys.table", required=True),
        "operation": Choice("Operation", OPERATIONS, required=True),
        # Only for records matching this condition (the filter language); empty: every record.
        "condition": Json("Only for records where"),
        # Only these fields (a list of names); empty: every field.
        "fields": Json("Only these fields"),
    },
    label_plural="Grants",
)

GROUP_ROLE = Table(
    "group_role",
    "Role of a group",
    {
        "group": Reference("Group", "sys.group", required=True),
        "role": Reference("Role", "sys.role", required=True),
        # Narrows the role for this group only, like a grant's condition.
        "condition": Json("Only for records where"),
    },
    label_plural="Roles of groups",
    unique_together=(("group", "role"),),
)

TABLES = [GROUP, MEMBERSHIP, ROLE, ROLE_INCLUDE, GRANT, GROUP_ROLE]
