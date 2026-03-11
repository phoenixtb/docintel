"""FastAPI dependency injection helpers."""

from typing import Annotated

from fastapi import Depends

from ..config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]
