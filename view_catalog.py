from dataclasses import dataclass
import threading
import time

from google.cloud import bigquery

from config import BIGQUERY_PROJECT_ID

DEFAULT_VIEW_ID = "nsp_base_view_01"


@dataclass(frozen=True)
class ApprovedView:
    dataset: str
    view: str
    display_name: str
    domain: str
    description: str
    project: str = BIGQUERY_PROJECT_ID
    available_years: tuple[int, ...] = ()

    @property
    def full_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.view}"

    @property
    def source_id(self) -> str:
        return self.view

    @property
    def object_type(self) -> str:
        return "View"


@dataclass(frozen=True)
class ApprovedTable:
    dataset: str
    table: str
    display_name: str
    domain: str
    description: str
    project: str = BIGQUERY_PROJECT_ID

    @property
    def full_id(self) -> str:
        return f"{self.project}.{self.dataset}.{self.table}"

    @property
    def source_id(self) -> str:
        return f"{self.dataset}.{self.table}"

    @property
    def object_type(self) -> str:
        return "Table"


_NSP = "dw_nsp_api_views"
_CRM = "dw_ms_crm_onprem_views"
_SP = "dw_sharepoint_views"

APPROVED_TABLE_DATASETS = {
    "dw_nsp_api": ("NSP source tables", "NSP API source data."),
    "dw_ms_crm_onprem": ("CRM source tables", "Microsoft Dynamics CRM source data."),
    "dw_sharepoint": ("SharePoint source tables", "SharePoint reporting source data."),
}

_table_cache: tuple[float, tuple[ApprovedTable, ...]] | None = None
_table_cache_lock = threading.Lock()
_TABLE_CACHE_TTL_SECONDS = 300

APPROVED_VIEWS: tuple[ApprovedView, ...] = (
    ApprovedView(_NSP, "nsp_base_view_01", "NSP Base View", "Athlete registration",
                 "National Sports Platform athlete registrations: athletes, sports, federations, regions, age groups, gender, nationality, registration status."),
    ApprovedView(_CRM, "fr_base_view_01", "FR Base View", "Facility reservations",
                 "Facility reservation (booking) records: facilities, bookings, reservation dates and status."),
    ApprovedView(_CRM, "nafes_licenses_view_01", "Nafes Licenses View", "Nafes licenses",
                 "Nafes licenses issued or held: license type, status, validity, holders."),
    ApprovedView(_CRM, "nafes_requests_view_01", "Nafes Requests View", "Nafes requests",
                 "Nafes licensing requests submitted: request type, status, submission dates."),
    ApprovedView(_SP, "budget_control_view_01", "Budget Control View", "Budget management",
                 "Budget control and tracking figures (spending against budget)."),
    ApprovedView(_SP, "budget_view_01", "Budget View", "Budget management",
                 "Budget allocations by year and item."),
    ApprovedView(_SP, "consumer_price_index_view_01", "Consumer Price Index View", "Economic statistics",
                 "Consumer price index (CPI) statistics."),
    ApprovedView(_SP, "cultural_and_entertainment_statistics_2023_cultural_events_region_free_time_sports_reg",
                 "Cultural Events by Region - Free Time Sports View (2023)", "Sports participation",
                 "2023 cultural and entertainment statistics by region for free-time sports."),
    ApprovedView(_SP, "cultural_and_entertainment_statistics_2023_cultural_events_region_sports_reg",
                 "Cultural Events by Region - Sports View (2023)", "Sports participation",
                 "2023 cultural and entertainment statistics by region for sports."),
    ApprovedView(_SP, "cultural_and_entertainment_statistics_cultural_events_gender",
                 "Cultural Events by Gender View", "Sports participation",
                 "Cultural and entertainment event statistics broken down by gender."),
    ApprovedView(_SP, "culture_and_entertainment_indicators_2024_administrative_region_2",
                 "Culture and Entertainment Indicators by Administrative Region View (2024)", "Sports participation",
                 "2024 culture and entertainment indicators by administrative region."),
    ApprovedView(_SP, "culture_and_entertainment_indicators_least_one_sports",
                 "Sports Event Attendance Indicators View", "Sports participation",
                 "Indicators of attending at least one sports event."),
    ApprovedView(_SP, "culture_and_entertainment_indicators_sports",
                 "Sports Participation Indicators View", "Sports participation",
                 "Sports participation indicators from culture and entertainment surveys."),
    ApprovedView(_SP, "health_determinants_statistics_publication_view_01",
                 "Health Determinants Statistics View", "Public health",
                 "Health determinants statistics publication (lifestyle, activity, health indicators)."),
    ApprovedView(_SP, "hrsd_v1_21_12_2025_hrsd_headeng", "HRSD Workforce Statistics View", "Workforce statistics",
                 "HRSD workforce and employment statistics."),
    ApprovedView(_SP, "manual_file_manual_hrsd_v3_1", "Community Sports Statistics View", "Community sports",
                 "Manually maintained community sports statistics (HRSD v3.1 file)."),
    ApprovedView(_SP, "population_estimates_statistics_view_01", "Population Estimates Statistics View",
                 "Population statistics", "Population estimates by year, age, gender, nationality or region."),
    ApprovedView(_SP, "sopc_view_01", "Sports Competitions and Athlete Performance View",
                 "Athlete competitions and medals",
                 "Sports competitions and athlete performance: events, results, medals."),
    ApprovedView(_SP, "sports_medicine_view_01", "Sports Medicine View 01", "Sports medicine",
                 "Sports medicine data set 1 (injuries, examinations, medical services). Its actual and forecast fields are STRING; it has no source_file or sheet_name fields."),
    ApprovedView(_SP, "sports_medicine_view_02", "Sports Medicine View 02", "Sports medicine",
                 "Sports medicine data set 2 (injuries, examinations, medical services). Its actual and forecast fields are NUMERIC; source_file and sheet_name are available."),
    ApprovedView(_SP, "vw_health_determinants_statistics_publication", "Health Determinants by Age Group View",
                 "Public health", "Health determinants statistics broken down by age group; verified reporting year 2023 only.", available_years=(2023,)),
)

# Pairs of view names whose JOIN keys have been verified. Empty: no relationship may be assumed.
VERIFIED_JOINS: tuple[frozenset[str], ...] = ()

_BY_VIEW = {v.view.lower(): v for v in APPROVED_VIEWS}


def get_view(view_id: str) -> ApprovedView | None:
    return _BY_VIEW.get((view_id or "").lower())


def clear_table_cache() -> None:
    global _table_cache
    with _table_cache_lock:
        _table_cache = None


def get_approved_tables() -> tuple[ApprovedTable, ...]:
    """Discover physical tables only in the three explicitly approved warehouse datasets."""
    global _table_cache
    with _table_cache_lock:
        cached = _table_cache
        if cached and time.monotonic() - cached[0] < _TABLE_CACHE_TTL_SECONDS:
            return cached[1]

    client = bigquery.Client(project=BIGQUERY_PROJECT_ID)
    tables = []
    for dataset, (domain, description) in APPROVED_TABLE_DATASETS.items():
        try:
            for item in client.list_tables(f"{BIGQUERY_PROJECT_ID}.{dataset}"):
                if item.table_type != "TABLE":
                    continue
                tables.append(
                    ApprovedTable(
                        dataset=dataset,
                        table=item.table_id,
                        display_name=item.table_id,
                        domain=domain,
                        description=description,
                    )
                )
        except Exception as error:
            print(f"\nERROR LOADING TABLE CATALOG FOR {dataset}: {type(error).__name__}: {error}")

    result = tuple(tables)
    with _table_cache_lock:
        _table_cache = (time.monotonic(), result)
    return result


def get_source(source_id: str) -> ApprovedView | ApprovedTable | None:
    view = get_view(source_id)
    if view:
        return view
    normalized = (source_id or "").casefold()
    return next(
        (table for table in get_approved_tables() if table.source_id.casefold() == normalized),
        None,
    )


def find_view(project: str, dataset: str, view: str) -> ApprovedView | None:
    found = get_view(view)
    if found and found.dataset.lower() == (dataset or "").lower() and found.project.lower() == (project or "").lower():
        return found
    return None


def find_source(project: str, dataset: str, object_name: str) -> ApprovedView | ApprovedTable | None:
    view = find_view(project, dataset, object_name)
    if view:
        return view
    if project.lower() != BIGQUERY_PROJECT_ID.lower() or dataset.lower() not in APPROVED_TABLE_DATASETS:
        return None
    return get_source(f"{dataset}.{object_name}")


def is_join_verified(view_ids: set[str]) -> bool:
    ids = {v.lower() for v in view_ids}
    return len(ids) <= 1 or any(ids <= {n.lower() for n in pair} for pair in VERIFIED_JOINS)
