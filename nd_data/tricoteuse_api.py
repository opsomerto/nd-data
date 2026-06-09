"""
Generic client for Tricoteuses API.

For the data models, we use datamodel-code-generator to generate pydantic models from the openapi spec of tricoteuses API

uv run datamodel-codegen \
    --url https://git.tricoteuses.fr/logiciels/tricoteuses-api-parlement/raw/branch/staging/prisma/openapi/openapi.yaml \
    --input-file-type openapi \
    --output tricoteuse_models.py \
    --force-optional

"""

import logging
from dataclasses import dataclass, field
from typing import (
    Dict,
    List,
    Optional,
    Type,
    TypeVar,
    Any,
    Generic,
    overload,
    Literal,
    Union,
)

import httpx
from pydantic import BaseModel

from nd_data.tricoteuse_models import (
    Acteur,
    ActeLegislatif,
    Agenda,
    Debat,
    Document,
    Dossier,
    Amendement,
    Mandat,
    Organe,
    Paragraphe,
    Subdivision,
)

# Configure logging
logger = logging.getLogger(__name__)

# Type variable for generic model handling
T = TypeVar("T", bound=BaseModel)


@dataclass
class ResourceConfig:
    """Configuration for a Tricoteuses API resource."""

    endpoint: str  # The API endpoint path (plural form, e.g., "dossiers")
    model_class: Type[BaseModel]  # The Pydantic model class for this resource


@dataclass
class ResourceResponse(Generic[T]):
    """
    Wrapper for API responses that includes both the model data and computed fields.

    Computed fields (like _count.amendements) are extracted from the raw response
    and made available separately from the validated model.
    """

    data: T
    _computed: Dict[str, Any] = field(default_factory=dict)

    def count(self, field_name: str) -> int:
        """Get a count value from _computed, returns 0 if not found."""
        return self._computed.get("_count", {}).get(field_name, 0)


# Mapping between resource names and their configurations
# Note: Some API endpoints have different names than the model classes:
#   - reunions -> Agenda
#   - interventions -> Paragraphe
RESOURCE_CONFIGS: Dict[str, ResourceConfig] = {
    "dossier": ResourceConfig(endpoint="dossiers", model_class=Dossier),
    "acte_legislatif": ResourceConfig(endpoint="actes-legislatifs", model_class=ActeLegislatif),
    "document": ResourceConfig(endpoint="documents", model_class=Document),
    "reunion": ResourceConfig(endpoint="reunions", model_class=Agenda),  # reunion -> Agenda
    "agenda": ResourceConfig(endpoint="reunions", model_class=Agenda),  # alias
    "debat": ResourceConfig(endpoint="debats", model_class=Debat),
    "intervention": ResourceConfig(
        endpoint="interventions", model_class=Paragraphe
    ),  # intervention -> Paragraphe
    "paragraphe": ResourceConfig(endpoint="interventions", model_class=Paragraphe),  # alias
    "mandat": ResourceConfig(endpoint="mandats", model_class=Mandat),
    "organe": ResourceConfig(endpoint="organes", model_class=Organe),
    "acteur": ResourceConfig(endpoint="acteurs", model_class=Acteur),
    "amendement": ResourceConfig(endpoint="amendements", model_class=Amendement),
}


class TricoteuseAPIError(Exception):
    """Base exception for Tricoteuses API errors."""

    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(self.message)


class TricoteuseAPIClient:
    """
    Generic client for the Tricoteuses API.

    Usage:
        client = TricoteuseAPIClient()

        # Get a single resource
        dossier = client.get("dossier", "DLR5L17N53000")
        debat = client.get("debat", "DEBAT123", include=["paragraphes"])

        # Get a list of resources
        dossiers = client.get_list("dossier", page=1, per_page=10, sort="dateDepot.desc")

        # Using typed methods
        dossier = client.get_dossier("DLR5L17N53000", include=["actesLegislatifs"])
    """

    DEFAULT_BASE_URL = "https://parlement.tricoteuses.fr"
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.Client] = None,
    ):
        """
        Initialize the Tricoteuses API client.

        Args:
            base_url: Base URL for the API (default: https://app.tricoteuses.fr)
            timeout: Request timeout in seconds (default: 30.0)
            http_client: Optional custom httpx.Client instance
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = http_client

    @property
    def client(self) -> httpx.Client:
        """Lazy initialization of the HTTP client."""
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, verify=False)
        return self._client

    def _build_url(self, endpoint: str, uid: Optional[str] = None) -> str:
        """Build the full URL for an API endpoint."""
        if uid:
            return f"{self.base_url}/{endpoint}/{uid}"
        return f"{self.base_url}/{endpoint}"

    def _build_params(
        self,
        include: Optional[List[str]] = None,
        sort: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        **extra_params: Any,
    ) -> Dict[str, Any]:
        """Build query parameters for the request."""
        params = {}

        if include:
            params["include"] = ",".join(include)
        if sort:
            params["sort"] = sort
        if page is not None:
            params["page"] = page
        if per_page is not None:
            params["perPage"] = per_page

        # Add any extra parameters
        for key, value in extra_params.items():
            if value is not None:
                params[key] = value

        return params

    def _handle_response(
        self,
        response: httpx.Response,
        resource_name: str,
        uid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Handle the API response, raising appropriate errors for failures."""
        if response.status_code == 404:
            uid_info = f" with uid '{uid}'" if uid else ""
            logger.warning(f"Resource '{resource_name}'{uid_info} not found (404)")
            raise TricoteuseAPIError(
                f"Resource '{resource_name}'{uid_info} not found",
                status_code=404,
                response_body=response.text,
            )

        if response.status_code >= 400:
            logger.error(
                f"API error for '{resource_name}': status={response.status_code}, body={response.text[:500]}"
            )
            raise TricoteuseAPIError(
                f"API request failed for '{resource_name}'",
                status_code=response.status_code,
                response_body=response.text,
            )

        try:
            return response.json()
        except Exception as e:
            logger.error(f"Failed to parse JSON response for '{resource_name}': {e}")
            raise TricoteuseAPIError(
                f"Failed to parse API response for '{resource_name}'",
                response_body=response.text,
            )

    @staticmethod
    def _normalize_raw_item(resource_name: str, raw_item: Dict[str, Any]) -> Dict[str, Any]:
        """Patch known API/model shape drift before Pydantic validation."""
        if resource_name != "amendement":
            return raw_item
        normalized = dict(raw_item)
        for key in ("listeProgrammesPLFR", "listeProgrammesPLF"):
            if isinstance(normalized.get(key), list):
                normalized[key] = None
        return normalized

    def _validate_model(self, resource_name: str, raw_item: Dict[str, Any]) -> BaseModel:
        config = RESOURCE_CONFIGS[resource_name]
        return config.model_class.model_validate(self._normalize_raw_item(resource_name, raw_item))

    @staticmethod
    def _extract_computed(raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract computed fields (like _count) from raw API response.

        Returns a dict with computed fields, e.g. {"_count": {"amendements": 42}}
        """
        computed = {}
        if "_count" in raw_data:
            computed["_count"] = raw_data["_count"]
        return computed

    def get(
        self,
        resource_name: str,
        uid: str,
        include: Optional[List[str]] = None,
        **extra_params: Any,
    ) -> Optional[BaseModel]:
        """
        Get a single resource by its UID.

        Args:
            resource_name: Name of the resource (e.g., "dossier", "debat", "reunion")
            uid: Unique identifier of the resource
            include: List of related resources to include in the response
            **extra_params: Additional query parameters

        Returns:
            The parsed resource model, or None if not found

        Raises:
            TricoteuseAPIError: If the API request fails (except 404)
            ValueError: If the resource_name is not recognized
        """
        if resource_name not in RESOURCE_CONFIGS:
            raise ValueError(
                f"Unknown resource '{resource_name}'. Available resources: {list(RESOURCE_CONFIGS.keys())}"
            )

        config = RESOURCE_CONFIGS[resource_name]
        url = self._build_url(config.endpoint, uid)
        params = self._build_params(include=include, **extra_params)

        logger.debug(f"GET {url} params={params}")

        try:
            response = self.client.get(url, params=params)
            data = self._handle_response(response, resource_name, uid)
            return self._validate_model(resource_name, data.get("data"))
        except TricoteuseAPIError as e:
            if e.status_code == 404:
                return None
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error for '{resource_name}' uid='{uid}': {e}")
            raise TricoteuseAPIError(f"Request failed for '{resource_name}': {e}")

    def get_list(
        self,
        resource_name: str,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **extra_params: Any,
    ) -> List[BaseModel]:
        """
        Get a list of resources with pagination.

        Args:
            resource_name: Name of the resource (e.g., "dossier", "debat")
            page: Page number (default: 1)
            per_page: Number of items per page (default: 10)
            sort: Sort order (e.g., "dateDepot.desc")
            include: List of related resources to include
            **extra_params: Additional query parameters (e.g., chambre="AN")

        Returns:
            List of parsed resource models

        Raises:
            TricoteuseAPIError: If the API request fails
            ValueError: If the resource_name is not recognized
        """
        if resource_name not in RESOURCE_CONFIGS:
            raise ValueError(
                f"Unknown resource '{resource_name}'. Available resources: {list(RESOURCE_CONFIGS.keys())}"
            )

        config = RESOURCE_CONFIGS[resource_name]
        url = self._build_url(config.endpoint)
        params = self._build_params(
            include=include,
            sort=sort,
            page=page,
            per_page=per_page,
            **extra_params,
        )

        logger.debug(f"GET {url} params={params}")

        try:
            response = self.client.get(url, params=params)
            data = self._handle_response(response, resource_name)
            items = data.get("data", [])
            return [self._validate_model(resource_name, item) for item in items]
        except httpx.RequestError as e:
            logger.error(f"Request error for '{resource_name}' list: {e}")
            raise TricoteuseAPIError(f"Request failed for '{resource_name}' list: {e}")

    def get_list_total(
        self,
        resource_name: str,
        **extra_params: Any,
    ) -> Optional[int]:
        """Return the total number of matching resources from API pagination headers."""
        if resource_name not in RESOURCE_CONFIGS:
            raise ValueError(
                f"Unknown resource '{resource_name}'. Available resources: {list(RESOURCE_CONFIGS.keys())}"
            )

        config = RESOURCE_CONFIGS[resource_name]
        url = self._build_url(config.endpoint)
        params = self._build_params(page=1, per_page=1, **extra_params)

        logger.debug(f"GET {url} params={params}")

        try:
            response = self.client.get(url, params=params)
            self._handle_response(response, resource_name)
            total = response.headers.get("total")
            return int(total) if total is not None else None
        except ValueError:
            return None
        except httpx.RequestError as e:
            logger.error(f"Request error for '{resource_name}' total: {e}")
            raise TricoteuseAPIError(f"Request failed for '{resource_name}' total: {e}")

    def get_with_computed(
        self,
        resource_name: str,
        uid: str,
        include: Optional[List[str]] = None,
        **extra_params: Any,
    ) -> Optional[ResourceResponse[BaseModel]]:
        """
        Get a single resource by its UID, including computed fields like _count.

        Args:
            resource_name: Name of the resource (e.g., "dossier", "debat")
            uid: Unique identifier of the resource
            include: List of related resources/computed fields to include
                     (e.g., ["actesLegislatifs", "_count.amendements"])
            **extra_params: Additional query parameters

        Returns:
            ResourceResponse containing the model and computed fields, or None if not found
        """
        if resource_name not in RESOURCE_CONFIGS:
            raise ValueError(
                f"Unknown resource '{resource_name}'. Available resources: {list(RESOURCE_CONFIGS.keys())}"
            )

        config = RESOURCE_CONFIGS[resource_name]
        url = self._build_url(config.endpoint, uid)
        params = self._build_params(include=include, **extra_params)

        logger.debug(f"GET {url} params={params}")

        try:
            response = self.client.get(url, params=params)
            response_data = self._handle_response(response, resource_name, uid)
            raw_data = response_data.get("data", {})
            model = self._validate_model(resource_name, raw_data)
            computed = self._extract_computed(raw_data)
            return ResourceResponse(data=model, _computed=computed)
        except TricoteuseAPIError as e:
            if e.status_code == 404:
                return None
            raise
        except httpx.RequestError as e:
            logger.error(f"Request error for '{resource_name}' uid='{uid}': {e}")
            raise TricoteuseAPIError(f"Request failed for '{resource_name}': {e}")

    def get_list_with_computed(
        self,
        resource_name: str,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **extra_params: Any,
    ) -> List[ResourceResponse[BaseModel]]:
        """
        Get a list of resources with pagination, including computed fields like _count.

        Args:
            resource_name: Name of the resource (e.g., "dossier", "debat")
            page: Page number (default: 1)
            per_page: Number of items per page (default: 10)
            sort: Sort order (e.g., "dateDepot.desc")
            include: List of related resources/computed fields to include
                     (e.g., ["actesLegislatifs", "_count.amendements"])
            **extra_params: Additional query parameters

        Returns:
            List of ResourceResponse containing models and computed fields
        """
        if resource_name not in RESOURCE_CONFIGS:
            raise ValueError(
                f"Unknown resource '{resource_name}'. Available resources: {list(RESOURCE_CONFIGS.keys())}"
            )

        config = RESOURCE_CONFIGS[resource_name]
        url = self._build_url(config.endpoint)
        params = self._build_params(
            include=include,
            sort=sort,
            page=page,
            per_page=per_page,
            **extra_params,
        )

        logger.debug(f"GET {url} params={params}")

        try:
            response = self.client.get(url, params=params)
            response_data = self._handle_response(response, resource_name)
            items = response_data.get("data", [])
            results = []
            for raw_item in items:
                model = self._validate_model(resource_name, raw_item)
                computed = self._extract_computed(raw_item)
                results.append(ResourceResponse(data=model, _computed=computed))
            return results
        except httpx.RequestError as e:
            logger.error(f"Request error for '{resource_name}' list: {e}")
            raise TricoteuseAPIError(f"Request failed for '{resource_name}' list: {e}")

    # -------------------------------------------------------------------------
    # Typed convenience methods for each resource
    # -------------------------------------------------------------------------

    @overload
    def get_dossier(
        self,
        uid: str,
        include: Optional[List[str]] = None,
        computed_fields: None = None,
        **kwargs,
    ) -> Optional[Dossier]: ...

    @overload
    def get_dossier(
        self,
        uid: str,
        include: Optional[List[str]] = None,
        computed_fields: List[str] = ...,
        **kwargs,
    ) -> Optional[ResourceResponse[Dossier]]: ...

    def get_dossier(
        self,
        uid: str,
        include: Optional[List[str]] = None,
        computed_fields: Optional[List[str]] = None,
        **kwargs,
    ) -> Union[Optional[Dossier], Optional[ResourceResponse[Dossier]]]:
        """
        Get a Dossier by its UID.

        Args:
            uid: Unique identifier of the dossier
            include: List of related resources to include in the response
            computed_fields: List of computed fields to include (e.g., ["_count.amendements"])
                             If provided, returns ResourceResponse with computed fields.
            **kwargs: Additional query parameters

        Includable relations:
            - acteurPrincipalRef, dossierAbsorbantRef, dossierAbsorbant
            - organeRef, commissionANRef, commissionSNRef, documentDeposeRef
            - rapporteurs, plf, initiateurs, actesLegislatifs
            - amendements, documents, pointsOdj, paragraphes, alineas, scrutins

        Computed fields:
            - _count.amendements, _count.documents, _count.actesLegislatifs, etc.
        """
        all_includes = (include or []) + (computed_fields or [])
        if computed_fields:
            return self.get_with_computed("dossier", uid, include=all_includes, **kwargs)
        return self.get("dossier", uid, include=include, **kwargs)

    @overload
    def get_dossiers(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: str = "dateDepot.desc",
        include: Optional[List[str]] = None,
        chambre: Optional[str] = None,
        computed_fields: None = None,
        **kwargs,
    ) -> List[Dossier]: ...

    @overload
    def get_dossiers(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: str = "dateDepot.desc",
        include: Optional[List[str]] = None,
        chambre: Optional[str] = None,
        computed_fields: List[str] = ...,
        **kwargs,
    ) -> List[ResourceResponse[Dossier]]: ...

    def get_dossiers(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: str = "dateDepot.desc",
        include: Optional[List[str]] = None,
        chambre: Optional[str] = None,
        computed_fields: Optional[List[str]] = None,
        **kwargs,
    ) -> Union[List[Dossier], List[ResourceResponse[Dossier]]]:
        """
        Get a list of Dossiers with pagination.

        Args:
            page: Page number (default: 1)
            per_page: Number of items per page (default: 10)
            sort: Sort order (default: "dateDepot.desc")
            include: List of related resources to include
            chambre: Filter by chamber ("AN" or "SN")
            computed_fields: List of computed fields to include (e.g., ["_count.amendements"])
                             If provided, returns List[ResourceResponse] with computed fields.
            **kwargs: Additional query parameters

        Computed fields:
            - _count.amendements, _count.documents, _count.actesLegislatifs, etc.
        """
        all_includes = (include or []) + (computed_fields or [])
        if computed_fields:
            return self.get_list_with_computed(
                "dossier",
                page=page,
                per_page=per_page,
                sort=sort,
                include=all_includes,
                chambre=chambre,
                **kwargs,
            )
        return self.get_list(
            "dossier",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            chambre=chambre,
            **kwargs,
        )

    def get_dossiers_total(self, chambre: Optional[str] = None, **kwargs) -> Optional[int]:
        """Return the total number of dossiers matching the provided filters."""
        return self.get_list_total("dossier", chambre=chambre, **kwargs)

    def get_acte_legislatif(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[ActeLegislatif]:
        """Get an ActeLegislatif by its UID."""
        return self.get("acte_legislatif", uid, include=include, **kwargs)

    def get_actes_legislatifs(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **kwargs,
    ) -> List[ActeLegislatif]:
        """Get a list of ActesLegislatifs with pagination."""
        return self.get_list(
            "acte_legislatif",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            **kwargs,
        )

    def get_document(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Document]:
        """Get a Document by its UID."""
        return self.get("document", uid, include=include, **kwargs)

    def get_documents(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Document]:
        """Get a list of Documents with pagination."""
        return self.get_list(
            "document",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            **kwargs,
        )

    def get_reunion(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Agenda]:
        """Get a Reunion (Agenda) by its UID."""
        return self.get("reunion", uid, include=include, **kwargs)

    def get_reunions(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Agenda]:
        """Get a list of Reunions (Agendas) with pagination."""
        return self.get_list(
            "reunion",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            **kwargs,
        )

    def get_debat(self, uid: str, include: Optional[List[str]] = None, **kwargs) -> Optional[Debat]:
        """
        Get a Debat by its UID.

        Includable relations:
            - paragraphes
        """
        return self.get("debat", uid, include=include, **kwargs)

    def get_debats(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        reunionRefUid: Optional[str] = None,
        **kwargs,
    ) -> List[Debat]:
        """Get a list of Debats with pagination."""
        return self.get_list(
            "debat",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            reunionRefUid=reunionRefUid,
            **kwargs,
        )

    def get_intervention(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Paragraphe]:
        """Get an Intervention (Paragraphe) by its UID."""
        return self.get("intervention", uid, include=include, **kwargs)

    def get_interventions(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: str = "ordreAbsoluSeance.asc",
        include: Optional[List[str]] = None,
        debatUid: Optional[str] = None,
        dossierRefUid: Optional[str] = None,
        **kwargs,
    ) -> List[Paragraphe]:
        """Get a list of Interventions (Paragraphes) with pagination."""
        return self.get_list(
            "intervention",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            debatUid=debatUid,
            dossierRefUid=dossierRefUid,
            **kwargs,
        )

    def get_amendement(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Amendement]:
        """Get an Amendement by its UID."""
        return self.get("amendement", uid, include=include, **kwargs)

    def get_amendements(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        dossierRefUid: Optional[str] = None,
        **kwargs,
    ) -> List[Amendement]:
        """Get a list of Amendements with pagination."""
        return self.get_list(
            "amendement",
            page=page,
            per_page=per_page,
            sort=sort,
            include=include,
            dossierRefUid=dossierRefUid,
            **kwargs,
        )

    def get_mandat(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Mandat]:
        """Get a Mandat by its UID."""
        return self.get("mandat", uid, include=include, **kwargs)

    def get_mandats(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Mandat]:
        """Get a list of Mandats with pagination."""
        return self.get_list(
            "mandat", page=page, per_page=per_page, sort=sort, include=include, **kwargs
        )

    def get_organe(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Organe]:
        """Get an Organe by its UID."""
        return self.get("organe", uid, include=include, **kwargs)

    def get_organes(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Organe]:
        """Get a list of Organes with pagination."""
        return self.get_list(
            "organe", page=page, per_page=per_page, sort=sort, include=include, **kwargs
        )

    def get_acteur(
        self, uid: str, include: Optional[List[str]] = None, **kwargs
    ) -> Optional[Acteur]:
        """Get an Acteur by its UID."""
        return self.get("acteur", uid, include=include, **kwargs)

    def get_acteurs(
        self,
        page: int = 1,
        per_page: int = 10,
        sort: Optional[str] = None,
        include: Optional[List[str]] = None,
        **kwargs,
    ) -> List[Acteur]:
        """Get a list of Acteurs with pagination."""
        return self.get_list(
            "acteur", page=page, per_page=per_page, sort=sort, include=include, **kwargs
        )

    def close(self):
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# -------------------------------------------------------------------------
# Module-level convenience functions (backward compatibility)
# -------------------------------------------------------------------------

# Default client instance for module-level functions
_default_client: Optional[TricoteuseAPIClient] = None


def _get_default_client() -> TricoteuseAPIClient:
    """Get or create the default client instance."""
    global _default_client
    if _default_client is None:
        _default_client = TricoteuseAPIClient()
    return _default_client


def get_dossier(uid: str, include: Optional[List[str]] = None) -> Optional[Dossier]:
    """
    Get a Dossier by its UID (backward-compatible function).

    Includable relations:
        - acteurPrincipalRef, dossierAbsorbantRef, dossierAbsorbant
        - organeRef, commissionANRef, commissionSNRef, documentDeposeRef
        - rapporteurs, plf, initiateurs, actesLegislatifs
        - amendements, documents, pointsOdj, paragraphes, alineas, scrutins
    """
    return _get_default_client().get_dossier(uid, include=include)


def get_document(uid: str) -> Optional[Document]:
    """Get a Document by its UID (backward-compatible function)."""
    return _get_default_client().get_document(uid)


def get_seance_acte_debat(client: TricoteuseAPIClient, acte: ActeLegislatif) -> Optional[Debat]:
    """ """
    # identify odj for the acte
    podj = [x for x in acte.agendaRef.pointsOdj if x.dossierLegislatifUid == acte.dossierRefUid]
    if not podj:
        return None
    elif len(podj) > 1:
        raise ValueError("Multiple points d'odj for acte")
    #
    ordre_point = str(podj[0].ordrePoint)

    # get the debat
    debat_uid = acte.agendaRef.compteRenduRefUid
    debat = client.get_debat(debat_uid, include=["paragraphes"])

    # filter paragraphs for the point d'odj
    paragraphs = [
        p
        for p in sorted(debat.paragraphes, key=lambda x: x.ordreAbsoluSeance)
        if p.valeurPtsOdj == ordre_point
    ]
    debat.paragraphes = paragraphs
    return debat
