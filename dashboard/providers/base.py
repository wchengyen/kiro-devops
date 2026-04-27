from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Resource:
    provider: str
    resource_type: str
    region: str
    id: str
    name: str
    status: str
    class_type: Optional[str] = None
    os_or_engine: Optional[str] = None
    tags: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        return f"{self.provider}:{self.resource_type}:{self.region}:{self.id}"


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float


@dataclass
class ResourceMetrics:
    resource_id: str
    metric_name: str
    points_7d: List[MetricPoint]
    points_30d: List[MetricPoint]
    current: Optional[float] = None
    stats_7d: Optional[Dict[str, Any]] = None
    stats_30d: Optional[Dict[str, Any]] = None
    sparkline_7d: List[float] = field(default_factory=list)


class BaseResourceProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def regions(self) -> List[str]: ...

    @abstractmethod
    def resource_types(self) -> List[str]: ...

    @abstractmethod
    def discover_resources(
        self, region: str, resource_type: Optional[str] = None
    ) -> List[Resource]: ...

    @abstractmethod
    def get_metrics(
        self, resource: Resource, range_days: int = 7
    ) -> ResourceMetrics: ...

    @abstractmethod
    def sync_metrics_to_store(self, store, backfill_days: int = 1) -> None:
        """Sync metrics to a store that implements write_raw(provider, timestamp, resource_id, metric, value).

        Args:
            store: A metrics store with a ``write_raw`` method (e.g. MetricsStore).
            backfill_days: Number of days to backfill when syncing.
        """
