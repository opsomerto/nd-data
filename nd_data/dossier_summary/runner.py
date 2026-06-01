"""High-level orchestration for dossier summary enrichment."""

from collections.abc import Iterable

from nd_data.dossier_summary.agents import (
    DEFAULT_MODEL,
    build_enrichment,
    run_combined,
    run_navette_situation,
    run_qualification,
    run_structured_summary,
)
from nd_data.dossier_summary.models import (
    DossierSummaryEnrichment,
    DossierSummaryOutput,
    SummaryModule,
)
from nd_data.dossier_summary.sources import DEFAULT_SOURCE_OPTIONS, build_dossier_input_pack
from nd_data.tricoteuse_api import TricoteuseAPIClient
from nd_data.tricoteuse_models import Dossier


def parse_modules(modules: Iterable[str | SummaryModule]) -> list[SummaryModule]:
    parsed: list[SummaryModule] = []
    for module in modules:
        if isinstance(module, SummaryModule):
            value = module
        elif module == "summary":
            value = SummaryModule.structured_summary
        elif module == "situation":
            value = SummaryModule.navette_situation
        else:
            value = SummaryModule(module)
        if value not in parsed:
            parsed.append(value)
    return parsed


def enrich_dossier_summary(
    dossier: Dossier,
    client: TricoteuseAPIClient | None = None,
    modules: Iterable[str | SummaryModule] = tuple(SummaryModule),
    source_options: Iterable[str] = DEFAULT_SOURCE_OPTIONS,
    model_name: str = DEFAULT_MODEL,
    combined: bool = False,
    include_pdf_text: bool = False,
    fetch_debate_durations: bool = False,
    fetch_amendment_count: bool = False,
    max_source_chars: int | None = None,
) -> DossierSummaryEnrichment:
    parsed_modules = parse_modules(modules)
    source_options = tuple(source_options)
    pack = build_dossier_input_pack(
        dossier,
        client=client,
        source_options=source_options,
        include_pdf_text=include_pdf_text,
        fetch_debate_durations=fetch_debate_durations,
        fetch_amendment_count=fetch_amendment_count,
        max_source_chars=max_source_chars,
    )

    if combined:
        combined_output = run_combined(pack, model_name=model_name)
        output = DossierSummaryOutput(
            qualification=(
                combined_output.qualification
                if SummaryModule.qualification in parsed_modules
                else None
            ),
            structured_summary=(
                combined_output.structured_summary
                if SummaryModule.structured_summary in parsed_modules
                else None
            ),
            navette_situation=(
                combined_output.navette_situation
                if SummaryModule.navette_situation in parsed_modules
                else None
            ),
        )
    else:
        output = DossierSummaryOutput()
        if SummaryModule.qualification in parsed_modules:
            output.qualification = run_qualification(pack, model_name=model_name)
        if SummaryModule.structured_summary in parsed_modules:
            output.structured_summary = run_structured_summary(pack, model_name=model_name)
        if SummaryModule.navette_situation in parsed_modules:
            output.navette_situation = run_navette_situation(pack, model_name=model_name)

    return build_enrichment(
        output=output,
        pack=pack,
        modules=parsed_modules,
        source_options=source_options,
        model_name=model_name,
    )
