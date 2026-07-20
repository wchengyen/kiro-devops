from __future__ import annotations

from .models import ConfigSnapshot, ExecutionContext, build_profile_fingerprint


class RouteNotFound(LookupError):
    pass


class TenantRouter:
    def __init__(self, snapshot: ConfigSnapshot):
        self._snapshot = snapshot
        self._group_routes = {
            (route.app_key, route.chat_id): route.profile_id
            for route in snapshot.routes
        }

    def resolve(
        self,
        *,
        platform: str,
        app_key: str,
        chat_type: str,
        chat_id: str | None,
        user_id: str,
    ) -> ExecutionContext:
        app = self._snapshot.apps.get(app_key)
        if app is None or not app.enabled:
            raise RouteNotFound(f"unknown app: {app_key}")

        if chat_type == "group":
            if not chat_id:
                raise RouteNotFound("group message is missing chat_id")
            profile_id = self._group_routes.get((app_key, chat_id))
            if profile_id is None:
                raise RouteNotFound(f"unmapped group: {app_key}/{chat_id}")
            group_scope_key = f"{platform}/{app_key}/group/{chat_id}"
            principal_key = f"{group_scope_key}/user/{user_id}"
        elif chat_type == "private":
            profile_id = app.default_profile
            group_scope_key = None
            principal_key = f"{platform}/{app_key}/private/{user_id}"
        else:
            raise RouteNotFound(f"unsupported chat_type: {chat_type}")

        profile = self._snapshot.profiles.get(profile_id)
        if profile is None or not profile.enabled:
            raise RouteNotFound(f"profile is unavailable: {profile_id}")

        return ExecutionContext(
            config_generation=self._snapshot.generation,
            platform=platform,
            app_key=app_key,
            chat_type=chat_type,
            chat_id=chat_id,
            user_id=user_id,
            principal_key=principal_key,
            group_scope_key=group_scope_key,
            profile_id=profile_id,
            profile=profile,
            profile_fingerprint=build_profile_fingerprint(profile),
        )
