from dataclasses import dataclass, replace
import threading
import time

from google.cloud import bigquery

from bigquery_catalog import CatalogUnavailableError, DiscoveredSource, discover_project_catalog
from config import (
    BIGQUERY_DYNAMIC_ALLOWED_DATASETS,
    BIGQUERY_DYNAMIC_CATALOG_ENABLED,
    BIGQUERY_DYNAMIC_ROUTING_ENABLED,
    BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES,
    BIGQUERY_PROJECT_ID,
)

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

# These are the physical tables authorized before discovery was separated from access control.
# New tables discovered in these datasets must be added here before routing or SQL validation can use them.
AUTHORIZED_TABLES = {
    "dw_nsp_api": frozenset("""
        accounts business_units contacts contacts_copy ldv_affiliationrequests
        ldv_athleteageclassifications ldv_bpfclubupdaterequests ldv_bpffederationrequests
        ldv_bpfindividualrequestsstances ldv_bpfnationalteamrequests ldv_bpfnewaffiliationrequests
        ldv_clubrequests ldv_clubs ldv_committees ldv_configurations ldv_contactaffiliationrecords
        ldv_contactaffiliationrecords_practice ldv_contactaffiliationrecords_status_1
        ldv_contactaffiliationrecords_test_3 ldv_countries ldv_countries_CDC ldv_departments
        ldv_eventtypes ldv_federationrequests ldv_federations ldv_guradiantemps ldv_individualids
        ldv_individualrequests ldv_messagelocalizations ldv_messages ldv_nationalities
        ldv_nationalteamrequests ldv_nationalteams ldv_recurringregistrationwindows
        ldv_requestheaders ldv_socialprofiles ldv_sportdisciplines ldv_sportdisciplinetemps
        ldv_sportsseasons ldv_staffdirectories nsp_option_set_lookup nsp_option_set_lookup_temp
    """.split()),
    "dw_ms_crm_onprem": frozenset("""
        AccountBase ContactBase gsadev_booksportsfacilityBase gsadev_facilityBase gsadev_subfacilityBase
        ntw_academicfacilityBase ntw_academiclicenseBase ntw_academiclicenserequestBase
        ntw_academicsportactivityBase ntw_authoityofficeBase ntw_cityBase ntw_clubactivityBase
        ntw_clubfacilityBase ntw_clublicenseBase ntw_clublicenserequestBase ntw_divingfacilityBase
        ntw_divinglicenseBase ntw_divinglicenserequestBase ntw_externalstatusBase ntw_facilityBase
        ntw_facilitytypeBase ntw_facilityutilizationrequestBase ntw_fineregistrationBase
        ntw_fineticketBase ntw_genderBase ntw_inspectiondetailBase ntw_inspectionrequestBase
        ntw_inspectiontypeBase ntw_licenseBase ntw_licenserequestBase ntw_licensetypeBase
        ntw_mainsportactivityBase ntw_modifylicenserequestBase ntw_regionBase ntw_renewlicenserequestBase
        ntw_requestedclubactivityBase ntw_requestedsporttrainingactivityBase ntw_requeststatusBase
        ntw_reservationeventtypeBase ntw_reservationscheduleBase ntw_sportactivitiesBase
        ntw_sportactivityBase ntw_sportstrainingactivityBase ntw_sportstrainingfacilityBase
        ntw_sportstraininginstituteslicenseBase ntw_sportstraininginstituteslicenserequestBase
        ntw_subsportactivityBase
    """.split()),
    "dw_sharepoint": frozenset("""
        budget budget_control consumer_price_index_table
        consumer_price_index_tables_nov_2025_ar_en_a_expenditure
        cultural_and_entertainment_statistics_2023_cultural_events_gender_2_5_sports_compitition
        cultural_and_entertainment_statistics_2023_cultural_events_gender_2_6_attended_sports
        cultural_and_entertainment_statistics_2023_cultural_events_gender_2_7_sports_in_free_time
        cultural_and_entertainment_statistics_2023_cultural_events_gender_2_8_practiced_in_free_time
        cultural_and_entertainment_statistics_2023_cultural_events_region_2_5_sports_reg
        cultural_and_entertainment_statistics_2023_cultural_events_region_2_6_attended_sports_reg
        cultural_and_entertainment_statistics_2023_cultural_events_region_2_7_free_time_sports_reg
        cultural_and_entertainment_statistics_2023_cultural_events_region_2_8_practiced_in_free_time_reg
        cultural_and_entertainment_statistics_2023_cultural_events_region_free_time_sports_reg
        cultural_and_entertainment_statistics_2023_cultural_events_region_sports_reg
        cultural_and_entertainment_statistics_cultural_events_gender
        culture_and_entertainment_indicators_2024_administrative_region_2
        culture_and_entertainment_indicators_2024_administrative_region_2_sheet_2_121618_2_12_attended_sportscompetition
        culture_and_entertainment_indicators_2024_administrative_region_2_sheet_2_121618_2_16_during_free_time
        culture_and_entertainment_indicators_2024_administrative_region_2_sheet_2_121618_2_18_participate_entertainment
        culture_and_entertainment_indicators_2024_administrative_region_sheet_2_10_n_2_14_2_10_at_least_one_sports
        culture_and_entertainment_indicators_2024_administrative_region_sheet_2_10_n_2_14_2_14at_least_one_entertainment
        culture_and_entertainment_indicators_2024_at_least_one_sports_sheet_2_9_n_2_13_2_13_at_least_one_entertainment
        culture_and_entertainment_indicators_2024_at_least_one_sports_sheet_2_9_n_2_13_2_9_at_least_one_sports
        culture_and_entertainment_indicators_2024_entertainment_events_and_activities_sheets_2_15_n_2_17_2_15during_free_time
        culture_and_entertainment_indicators_2024_entertainment_events_and_activities_sheets_2_15_n_2_17_2_17_participate_entertainment
        culture_and_entertainment_indicators_2024_sports_competitions_sheet_2_11_2_11attended_sports_competition
        culture_and_entertainment_indicators_least_one_sports culture_and_entertainment_indicators_sports
        health_2023_admin_6_3 health_2023_admin_6_6 health_2023_age_5_2 health_2023_age_5_5
        health_2023_weight_4_1 health_2023_weight_4_4 health_2024_age_2 health_2024_age_5
        health_2024_bmi_1 health_2024_bmi_4 health_2024_region_3 health_2024_region_6
        health_determinants_statistics_publication health_determinants_statistics_publication_01
        health_determinants_statistics_publication_2023_administrative_region_sheet_3_n_6_3_obesity_rate_among_15_up
        health_determinants_statistics_publication_2023_administrative_region_sheet_3_n_6_6_obesity_rate_less_than15
        health_determinants_statistics_publication_2023_age_group_sheet_2_n_5_2_obesity_rate_among_15_up
        health_determinants_statistics_publication_2023_age_group_sheet_2_n_5_5_obesity_rate_less_than_15
        health_determinants_statistics_publication_2023_weight_sheet_1_n_4_1_bmi_15_above
        health_determinants_statistics_publication_2023_weight_sheet_1_n_4_4_bmi_less_than_15
        health_determinants_statistics_publication_2024_en_body_mass_index_sex_n_nationality_1_bmi_15years_above
        health_determinants_statistics_publication_2024_en_body_mass_index_sex_n_nationality_4_bmi_2_14years
        health_determinants_statistics_publication_2024_en_obesity_prevalence_region_3_op_15_years_above
        health_determinants_statistics_publication_2024_en_obesity_prevalence_region_6_op_2_14_years
        health_determinants_statistics_publication_2024_en_obesity_prevalence_sex_nationality_n_age_groups_2_op_15_years_above
        health_determinants_statistics_publication_2024_en_obesity_prevalence_sex_nationality_n_age_groups_5_op_2_14_years
        hrsd_v1_21_12_2025_hrsd_headeng manual_file_manual_hrsd_v3_1 olympic_data
        population_estimates_statistics population_estimates_statistics_2024_en_1_age_groups
        sopc_26_01_2026_xlsx_merged_data sopc_26_01_2026_xlsx_merged_olympic_data_20260121_17
        sports_medicine_data_fact_year_metric_all
        تفاصيل_بنود_ميزانية_2023_sheet2 تفاصيل_بنود_ميزانية_2024_sheet2 تفاصيل_بنود_ميزانية_2025_fy25
        تفاصيل_بنود_ميزانية_2026_fy24 تفاصيل_بنود_ميزانية_2026_fy25 تفاصيل_بنود_ميزانية_2026_fy26
        تقرير_المنصرف_منصة_اعتماد_2023_sheet1 تقرير_المنصرف_منصة_اعتماد_2024_sheet1
        تقرير_المنصرف_منصة_اعتماد_2025_sheet1 تقرير_المنصرف_منصة_اعتماد_2026_sheet1
    """.split()),
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
    """Discover physical tables; discovery alone does not authorize query access."""
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


def get_discovered_sources(*, force_refresh: bool = False) -> tuple[DiscoveredSource, ...]:
    """Return project-wide metadata inventory; these objects are not query authorization."""
    if not BIGQUERY_DYNAMIC_CATALOG_ENABLED:
        return ()
    return discover_project_catalog(force_refresh=force_refresh).sources


def get_dynamic_authorized_sources() -> tuple[DiscoveredSource, ...]:
    """Return only discovered objects covered by explicit dataset and column policies."""
    if not BIGQUERY_DYNAMIC_CATALOG_ENABLED or not BIGQUERY_DYNAMIC_ROUTING_ENABLED:
        return ()
    if not BIGQUERY_DYNAMIC_ALLOWED_DATASETS or not BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES:
        return ()
    try:
        discovered_sources = get_discovered_sources()
    except CatalogUnavailableError as error:
        print(f"\nPROJECT CATALOG UNAVAILABLE: {type(error).__name__}; using static source catalog.")
        return ()
    policies = {
        source_id.casefold(): {column.casefold() for column in columns}
        for source_id, columns in BIGQUERY_DYNAMIC_SOURCE_COLUMN_POLICIES.items()
    }
    supported_types = {"TABLE", "VIEW", "MATERIALIZED_VIEW", "EXTERNAL_TABLE"}
    authorized = []
    for source in discovered_sources:
        if source.project.casefold() != BIGQUERY_PROJECT_ID.casefold():
            continue
        if source.dataset.casefold() not in BIGQUERY_DYNAMIC_ALLOWED_DATASETS:
            continue
        if source.object_type not in supported_types or source.metadata_error:
            continue
        allowed_columns = policies.get(source.source_id.casefold())
        if not allowed_columns:
            continue
        columns_by_name = {column.name.casefold(): column for column in source.columns}
        if not allowed_columns <= columns_by_name.keys():
            continue
        selected_columns = tuple(
            column
            for column in source.columns
            if column.name.casefold() in allowed_columns and not column.policy_tags
        )
        if not selected_columns:
            continue
        authorized.append(replace(source, columns=selected_columns, query_authorized=True))
    return tuple(authorized)


def get_authorized_tables() -> tuple[ApprovedTable, ...]:
    """Return discovered tables intersected with the explicit physical-table allowlist."""
    authorized = AUTHORIZED_TABLES
    return tuple(
        table
        for table in get_approved_tables()
        if table.table in authorized.get(table.dataset, frozenset())
    )


def get_source(source_id: str) -> ApprovedView | ApprovedTable | DiscoveredSource | None:
    view = get_view(source_id)
    if view:
        return view
    normalized = (source_id or "").casefold()
    static_table = next(
        (table for table in get_authorized_tables() if table.source_id.casefold() == normalized),
        None,
    )
    if static_table:
        return static_table
    return next(
        (source for source in get_dynamic_authorized_sources() if source.source_id.casefold() == normalized),
        None,
    )


def get_queryable_sources() -> tuple[ApprovedView | ApprovedTable | DiscoveredSource, ...]:
    """Sources eligible for routing and SQL validation; discovery-only records are excluded."""
    return (*APPROVED_VIEWS, *get_authorized_tables(), *get_dynamic_authorized_sources())


def find_view(project: str, dataset: str, view: str) -> ApprovedView | None:
    found = get_view(view)
    if found and found.dataset.lower() == (dataset or "").lower() and found.project.lower() == (project or "").lower():
        return found
    return None


def find_source(project: str, dataset: str, object_name: str) -> ApprovedView | ApprovedTable | DiscoveredSource | None:
    view = find_view(project, dataset, object_name)
    if view:
        return view
    if project.lower() != BIGQUERY_PROJECT_ID.lower():
        return None
    if dataset.lower() not in APPROVED_TABLE_DATASETS and dataset.lower() not in BIGQUERY_DYNAMIC_ALLOWED_DATASETS:
        return None
    return get_source(f"{dataset}.{object_name}")


def is_join_verified(view_ids: set[str]) -> bool:
    ids = {v.lower() for v in view_ids}
    return len(ids) <= 1 or any(ids <= {n.lower() for n in pair} for pair in VERIFIED_JOINS)
