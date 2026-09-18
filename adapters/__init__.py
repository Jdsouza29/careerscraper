"""Adapter registry. `companies.json` names one of these keys per company."""

from adapters.ashby import AshbyAdapter
from adapters.base import Adapter, AdapterError, make_job
from adapters.custom_html import CustomHtmlAdapter
from adapters.custom_json import CustomJsonAdapter
from adapters.eightfold import EightfoldAdapter
from adapters.greenhouse import GreenhouseAdapter
from adapters.lever import LeverAdapter
from adapters.oracle_ce import OracleCeAdapter
from adapters.smartrecruiters import SmartRecruitersAdapter
from adapters.workday import WorkdayAdapter

ADAPTERS = {
    cls.slug: cls
    for cls in (
        GreenhouseAdapter,
        LeverAdapter,
        AshbyAdapter,
        WorkdayAdapter,
        SmartRecruitersAdapter,
        EightfoldAdapter,
        OracleCeAdapter,
        CustomJsonAdapter,
        CustomHtmlAdapter,
    )
}

__all__ = ["ADAPTERS", "Adapter", "AdapterError", "make_job", "build_adapter"]


def build_adapter(company):
    slug = company.get("adapter")
    if slug not in ADAPTERS:
        raise AdapterError(
            f"{company.get('name', '?')}: unknown adapter {slug!r} "
            f"(known: {', '.join(sorted(ADAPTERS))})"
        )
    return ADAPTERS[slug](company)
