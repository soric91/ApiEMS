"""Response envelope compartido por toda la API.

Éxito:  {"success": true,  "message": "...", "data": ...}
Error:  {"success": false, "message": "...", "error": ...}
"""

from pydantic import BaseModel


class ApiResponse[DataT](BaseModel):
    success: bool = True
    message: str = "OK"
    data: DataT | None = None


class ApiError(BaseModel):
    success: bool = False
    message: str
    error: str | dict[str, object] | list[dict[str, object]] | None = None
