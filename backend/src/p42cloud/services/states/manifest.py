"""What state flows add to the core: their tables and the check on every change."""

from p42cloud.services.states import models, service

TABLES = models.TABLES
HOOKS = service.TABLE_HOOKS
