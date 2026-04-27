"""Shared constants for the pipeline package."""

from __future__ import annotations

ALL_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL",
    "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME",
    "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
    "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI",
    "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI",
    "WY",
]

AVAILABLE_SOURCES = ["echo", "rcra", "caa", "sdwa", "sems", "ucmr", "pfas", "tceq", "pa_dep", "pa_dep_gis", "oh_epa", "ny_dec", "ny_dec_gis", "fl_dep", "fl_dep_stcm", "fl_dep_chaz", "fl_dep_arms", "fl_dep_bf", "fl_dep_waste", "il_epa", "ca_dtsc", "ca_waterboard", "ca_geotracker", "nj_dep", "mi_egle", "nc_deq", "ga_epd", "wa_ecy", "ma_dep", "va_deq", "co_cdphe", "az_deq", "mn_pca", "la_deq", "mo_dnr", "md_mde", "in_idem", "sc_des", "tn_tdec", "ks_kdhe", "or_deq", "wi_dnr", "ct_deep", "al_adem", "ok_deq", "ia_dnr", "ky_dep", "ut_deq", "nv_dep", "ar_deq", "de_dnrec", "ms_mdeq", "wv_dep", "nm_nmed", "ne_dee", "me_dep", "nh_des", "nd_deq", "sd_danr", "mt_deq", "id_deq", "vt_dec", "wy_deq", "ak_dec", "ri_dem", "hi_doh", "dc_doee", "mi_pfas", "nj_pfas", "wi_pfas", "oh_pfas", "il_pfas"]

FEDERAL_SOURCES = {"epa_echo", "epa_rcra", "epa_caa", "epa_sdwa", "epa_sems", "epa_ucmr", "epa_pfas"}

# Re-export STATE_SOURCE_MAP from resolve.py for backward compat
from .resolve import STATE_SOURCE_MAP

__all__ = ["ALL_STATES", "AVAILABLE_SOURCES", "FEDERAL_SOURCES", "STATE_SOURCE_MAP"]
