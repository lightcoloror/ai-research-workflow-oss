from dataclasses import dataclass


@dataclass
class FetchRequest:
    url: str


@dataclass
class FetchResult:
    requested_url: str = ""
    final_url: str = ""
    status: str = "ok"
    extracted_text: str = ""
    raw_snapshot: str = ""
    content_type: str = "text/plain"
    transport: str = "contract_fixture"
    errors: list[str] | None = None


def fetch_content(request: FetchRequest) -> FetchResult:
    return FetchResult(
        requested_url=request.url,
        final_url=request.url,
        extracted_text="contract fixture content",
    )
