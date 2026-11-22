from __future__ import annotations

from functools import lru_cache
from typing import Any
import httpx

from app.config import get_settings


class QueryResult:
    def __init__(self, data: Any):
        self.data = data


class TableQuery:
    def __init__(self, client: SupabaseClient, table_name: str):
        self.client = client
        self.table_name = table_name
        self._action = "select"
        self._payload: Any = None
        self._params: dict[str, Any] = {}
        self._headers: dict[str, str] = {}
        self._single = False
        self._on_conflict: str | None = None

    def select(self, columns: str = "*") -> TableQuery:
        self._action = "select"
        self._params["select"] = columns
        return self

    def insert(self, data: Any) -> TableQuery:
        self._action = "insert"
        self._payload = data
        return self

    def upsert(self, data: Any, on_conflict: str | None = None) -> TableQuery:
        self._action = "upsert"
        self._payload = data
        self._on_conflict = on_conflict
        return self

    def update(self, data: dict[str, Any]) -> TableQuery:
        self._action = "update"
        self._payload = data
        return self

    def delete(self) -> TableQuery:
        self._action = "delete"
        return self

    def eq(self, column: str, value: Any) -> TableQuery:
        self._params[column] = f"eq.{value}"
        return self

    def gte(self, column: str, value: Any) -> TableQuery:
        self._params[column] = f"gte.{value}"
        return self

    def lte(self, column: str, value: Any) -> TableQuery:
        self._params[column] = f"lte.{value}"
        return self

    def order(self, column: str, desc: bool = False) -> TableQuery:
        direction = "desc" if desc else "asc"
        self._params["order"] = f"{column}.{direction}"
        return self

    def limit(self, count: int) -> TableQuery:
        self._params["limit"] = str(count)
        return self

    def single(self) -> TableQuery:
        self._single = True
        return self

    def execute(self) -> QueryResult:
        return self.client._execute(self)


class SupabaseClient:
    def __init__(self, url: str, key: str):
        self.url = url.rstrip("/")
        self.key = key
        self._mock_data: dict[str, list[dict[str, Any]]] = {}

    def table(self, table_name: str) -> TableQuery:
        return TableQuery(self, table_name)

    def _execute(self, query: TableQuery) -> QueryResult:
        # If url is placeholder or empty, use in-memory mock storage
        is_real_url = self.url.startswith("http://") or self.url.startswith("https://")
        is_placeholder = (
            not is_real_url
            or "YOUR_PROJECT" in self.url
            or "mock" in self.url
            or "example.com" in self.url
            or not self.key
            or "your-" in self.key
        )
        if is_placeholder:
            return self._execute_mock(query)

        # Real Supabase HTTP REST call
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
            **query._headers,
        }
        url = f"{self.url}/rest/v1/{query.table_name}"

        with httpx.Client(timeout=20) as http:
            if query._action == "select":
                r = http.get(url, params=query._params, headers=headers)
                data = r.json() if r.is_success else []
                if query._single:
                    data = data[0] if (isinstance(data, list) and data) else data
                return QueryResult(data)
            elif query._action == "insert":
                r = http.post(url, json=query._payload, headers=headers)
                return QueryResult(r.json() if r.is_success else [])
            elif query._action == "upsert":
                if query._on_conflict:
                    headers["Prefer"] = f"return=representation,resolution=merge-duplicates"
                r = http.post(url, json=query._payload, headers=headers)
                return QueryResult(r.json() if r.is_success else [])
            elif query._action == "update":
                r = http.patch(url, json=query._payload, params=query._params, headers=headers)
                return QueryResult(r.json() if r.is_success else [])
            elif query._action == "delete":
                r = http.delete(url, params=query._params, headers=headers)
                return QueryResult(r.json() if r.is_success else [])
            return QueryResult([])

    def _execute_mock(self, query: TableQuery) -> QueryResult:
        tbl = self._mock_data.setdefault(query.table_name, [])
        if query._action == "select":
            res = list(tbl)
            for k, v in query._params.items():
                if k in ("select", "order", "limit"):
                    continue
                if isinstance(v, str) and v.startswith("eq."):
                    target = v[3:]
                    res = [r for r in res if str(r.get(k)) == target]
                elif isinstance(v, str) and v.startswith("gte."):
                    target = v[4:]
                    res = [r for r in res if str(r.get(k, "")) >= target]
                elif isinstance(v, str) and v.startswith("lte."):
                    target = v[4:]
                    res = [r for r in res if str(r.get(k, "")) <= target]
            if "order" in query._params:
                order_spec = query._params["order"]
                col, _, direction = order_spec.partition(".")
                res.sort(key=lambda r: str(r.get(col, "")), reverse=(direction == "desc"))
            if "limit" in query._params:
                res = res[: int(query._params["limit"])]
            if query._single:
                return QueryResult(res[0] if res else {})
            return QueryResult(res)

        elif query._action == "insert":
            items = query._payload if isinstance(query._payload, list) else [query._payload]
            inserted = []
            for item in items:
                row = dict(item)
                if "id" not in row:
                    import uuid
                    row["id"] = str(uuid.uuid4())
                tbl.append(row)
                inserted.append(row)
            return QueryResult(inserted if isinstance(query._payload, list) else inserted)

        elif query._action == "upsert":
            item = dict(query._payload)
            conflict_keys = [k.strip() for k in (query._on_conflict or "id").split(",")]
            existing_idx = -1
            for idx, r in enumerate(tbl):
                if all(str(r.get(k)) == str(item.get(k)) for k in conflict_keys):
                    existing_idx = idx
                    break
            if existing_idx >= 0:
                tbl[existing_idx].update(item)
                return QueryResult([tbl[existing_idx]])
            else:
                if "id" not in item:
                    import uuid
                    item["id"] = str(uuid.uuid4())
                tbl.append(item)
                return QueryResult([item])

        elif query._action == "update":
            updated = []
            for r in tbl:
                match = True
                for k, v in query._params.items():
                    if isinstance(v, str) and v.startswith("eq.") and str(r.get(k)) != v[3:]:
                        match = False
                        break
                if match:
                    r.update(query._payload)
                    updated.append(r)
            return QueryResult(updated)

        elif query._action == "delete":
            deleted = []
            remaining = []
            for r in tbl:
                match = True
                for k, v in query._params.items():
                    if isinstance(v, str) and v.startswith("eq.") and str(r.get(k)) != v[3:]:
                        match = False
                        break
                if match:
                    deleted.append(r)
                else:
                    remaining.append(r)
            self._mock_data[query.table_name] = remaining
            return QueryResult(deleted)

        return QueryResult([])


_global_client: SupabaseClient | None = None


def get_supabase() -> SupabaseClient:
    global _global_client
    if _global_client is not None:
        return _global_client
    settings = get_settings()
    _global_client = SupabaseClient(settings.supabase_url, settings.supabase_service_role_key)
    return _global_client


def set_supabase_client(client: SupabaseClient) -> None:
    global _global_client
    _global_client = client


def get_profile_by_user_id(user_id: str) -> dict:
    client = get_supabase()
    result = (
        client.table("profiles")
        .select("*")
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise ValueError("Profile not found")
    return result.data[0]
