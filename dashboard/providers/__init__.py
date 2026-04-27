from dashboard.providers.aws import AWSProvider
from dashboard.providers.tencent import TencentProvider

_REGISTRY = {
    "aws": AWSProvider,
    "tencent": TencentProvider,
}


def get_provider(name: str):
    cls = _REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown provider: {name}")
    return cls()


def get_all_enabled_providers():
    providers = []
    for cls in _REGISTRY.values():
        p = cls()
        if p.is_enabled():
            providers.append(p)
    return providers
