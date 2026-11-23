from __future__ import annotations

PLATFORM_ADMIN_PAGE = "Platform Admin"


def application_pages(base_pages: list[str] | tuple[str, ...], *, login_is_enabled: bool) -> list[str]:
    """Return visible main-navigation pages for the current runtime mode.

    During local development login is deliberately disabled, so Platform Admin
    is exposed as a normal navigation destination for HTC development/UAT.
    Once login is enabled, Platform Admin is no longer exposed to customer
    navigation; the protected admin route remains the entry point.
    """
    pages = list(base_pages)
    if not login_is_enabled and PLATFORM_ADMIN_PAGE not in pages:
        pages.append(PLATFORM_ADMIN_PAGE)
    return pages
