"""Source id -> trade-layer connector."""
from __future__ import annotations

from app.trade import materials as m
from app.trade.comtrade import ComtradeConnector
from app.trade.eurostat import EurostatTradeConnector
from app.trade.registry import comtrade_window


def trade_connector_for(source_id: str):
    for material in m.MATERIALS:
        if source_id == m.eurostat_source_id(material):
            return EurostatTradeConnector(source_id, material)
        if material.eurostat_predecessor and source_id == m.eurostat_predecessor_source_id(material):
            return EurostatTradeConnector(source_id, material, predecessor=True)
    window = comtrade_window(source_id)
    if window is not None:
        _, frequency, first, last, _, kind = window
        return ComtradeConnector(source_id, frequency=frequency, first=first, last=last,
                                 bilateral_only=kind == "bilateral")
    return None
