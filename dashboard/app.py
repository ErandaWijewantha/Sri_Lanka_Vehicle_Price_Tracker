"""Streamlit dashboard (read-only).

    streamlit run dashboard/app.py

Reads DATABASE_URL from Streamlit secrets or the environment; point it at the
read-only `dashboard_ro` role (see db/schema.sql).
"""

from __future__ import annotations

import os
from typing import Any

import pandas as pd
import plotly.express as px
import psycopg
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Sri Lanka Vehicle Prices", layout="wide")

SERIES_COLOR = "#2a78d6"
TYPE_LABELS = {
    "car": "Car", "suv": "SUV", "van": "Van", "motorcycle": "Motorcycle",
    "three_wheel": "Three-wheel", "lorry": "Lorry", "pickup": "Pickup",
    "heavy_duty": "Heavy duty", "bus": "Bus", "tractor": "Tractor",
}
ASKING_NOTE = "Prices are **asking prices** from riyasewana.com and ikman.lk, not sale prices."


def _database_url() -> str:
    try:
        url = st.secrets.get("DATABASE_URL")
    except FileNotFoundError:
        url = None
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        st.error("DATABASE_URL is not configured (Streamlit secrets or environment).")
        st.stop()
    return url


@st.cache_data(ttl=600, show_spinner=False)
def query(sql: str, params: tuple[Any, ...] = ()) -> pd.DataFrame:
    with psycopg.connect(_database_url(), prepare_threshold=None) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def lkr(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "–"
    return f"Rs {value:,.0f}"


def type_label(t: str) -> str:
    return TYPE_LABELS.get(t, t)


def _style_fig(fig, y_title: str):
    fig.update_traces(line_width=2, marker_size=8)
    fig.update_layout(
        margin=dict(l=8, r=8, t=8, b=8),
        height=320,
        showlegend=False,
        xaxis_title=None,
        yaxis_title=y_title,
        hovermode="x unified",
    )
    fig.update_yaxes(rangemode="tozero", gridcolor="rgba(128,128,128,0.15)")
    fig.update_xaxes(showgrid=False)
    return fig


# --- Market -------------------------------------------------------------------

def page_market() -> None:
    st.title("Market overview")
    st.caption(ASKING_NOTE)

    kpi = query("""
        select
          count(*) filter (where status = 'active') as active,
          count(*) filter (where first_seen > now() - interval '7 days') as new_7d,
          count(*) filter (where delisted_at > now() - interval '7 days') as delisted_7d,
          (select count(*) from price_changes
            where changed_at > now() - interval '7 days' and new_price < old_price) as cuts_7d
        from listings
    """).iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Active ads", f"{kpi.active:,}")
    c2.metric("New this week", f"{kpi.new_7d:,}")
    c3.metric("Delisted this week", f"{kpi.delisted_7d:,}", help="Usually sold, sometimes expired.")
    c4.metric("Price cuts this week", f"{kpi.cuts_7d:,}")

    by_type = query("""
        select vehicle_type, count(*) as ads from listings
        where status = 'active' group by vehicle_type order by ads desc
    """)
    if not by_type.empty:
        by_type["type"] = by_type.vehicle_type.map(type_label)
        st.subheader("Active ads by vehicle type")
        fig = px.bar(by_type, x="ads", y="type", orientation="h", text="ads",
                     color_discrete_sequence=[SERIES_COLOR])
        fig.update_traces(texttemplate="%{text:,}", textposition="outside", cliponaxis=False,
                          hovertemplate="%{y}: %{x:,} active ads<extra></extra>")
        fig.update_layout(yaxis=dict(categoryorder="total ascending", title=None),
                          xaxis=dict(title=None, showgrid=False, showticklabels=False),
                          margin=dict(l=8, r=40, t=8, b=8), height=40 + 34 * len(by_type), bargap=0.35)
        st.plotly_chart(fig, width="stretch")

    st.subheader("Biggest price cuts")
    window = st.segmented_control("Window", ["24 hours", "7 days"], default="7 days",
                                  label_visibility="collapsed") or "7 days"
    interval = "1 day" if window == "24 hours" else "7 days"
    cuts = query(f"""
        select l.make, l.model_raw, l.year, l.vehicle_type, l.city, l.source,
               pc.old_price, pc.new_price, pc.changed_at, l.url
        from price_changes pc
        join listings l using (source, ad_id)
        where pc.changed_at > now() - interval '{interval}'
          and pc.old_price is not null and pc.new_price is not null
          and pc.new_price < pc.old_price
        order by (pc.old_price - pc.new_price)::float / pc.old_price desc
        limit 50
    """)
    if cuts.empty:
        st.info("No price cuts in this window yet.")
    else:
        cuts["drop_pct"] = (cuts.old_price - cuts.new_price) / cuts.old_price * 100
        cuts["vehicle"] = (cuts.make.fillna("") + " " + cuts.model_raw.fillna("")).str.strip()
        st.dataframe(
            cuts[["vehicle", "year", "old_price", "new_price", "drop_pct", "city", "source", "changed_at", "url"]],
            hide_index=True, width="stretch",
            column_config={
                "old_price": st.column_config.NumberColumn("Was (Rs)", format="localized"),
                "new_price": st.column_config.NumberColumn("Now (Rs)", format="localized"),
                "drop_pct": st.column_config.NumberColumn("Cut", format="%.1f%%"),
                "changed_at": st.column_config.DatetimeColumn("When", format="MMM D, HH:mm"),
                "url": st.column_config.LinkColumn("Ad", display_text="open"),
            },
        )

    st.subheader("Heating up / cooling down")
    st.caption("Change in active ads over the last 7 days, per model (all years combined), "
               "with new ads posted in that time. Models with fewer than 10 active ads are left out.")
    trend = query("""
        with latest as (select max(day) as d from daily_stats),
        agg as (
          select vehicle_type, make, model, day,
                 sum(active_count) as active, sum(new_count) as new
          from daily_stats, latest
          where day between latest.d - 7 and latest.d
          group by vehicle_type, make, model, day
        )
        select a.vehicle_type, a.make, a.model,
               a.active as active_now,
               coalesce(b.active, 0) as active_week_ago,
               (select sum(new) from agg x where x.vehicle_type = a.vehicle_type and x.make = a.make
                  and x.model = a.model and x.day > (select d from latest) - 7) as new_7d
        from agg a
        left join agg b on b.vehicle_type = a.vehicle_type and b.make = a.make and b.model = a.model
                       and b.day = (select d from latest) - 7
        where a.day = (select d from latest) and a.active >= 10
    """)
    if trend.empty or (trend.active_week_ago == 0).all():
        st.info("Needs at least a week of daily stats.")
    else:
        trend = trend[trend.active_week_ago > 0].copy()
        trend["change"] = trend.active_now - trend.active_week_ago
        trend["change_pct"] = trend.change / trend.active_week_ago * 100
        trend["vehicle"] = trend.make + " " + trend.model
        trend["type"] = trend.vehicle_type.map(type_label)
        cols = ["vehicle", "type", "active_week_ago", "active_now", "change_pct", "new_7d"]
        cfg = {
            "active_week_ago": st.column_config.NumberColumn("Active 7d ago"),
            "active_now": st.column_config.NumberColumn("Active now"),
            "change_pct": st.column_config.NumberColumn("Change", format="%+.1f%%"),
            "new_7d": st.column_config.NumberColumn("New ads (7d)"),
        }
        left, right = st.columns(2)
        left.markdown("**Heating up** (supply growing)")
        left.dataframe(trend.nlargest(15, "change_pct")[cols], hide_index=True,
                       width="stretch", column_config=cfg)
        right.markdown("**Cooling down** (supply shrinking)")
        right.dataframe(trend.nsmallest(15, "change_pct")[cols], hide_index=True,
                        width="stretch", column_config=cfg)


# --- Model trend --------------------------------------------------------------

def page_model_trend() -> None:
    st.title("Model trend")
    st.caption(ASKING_NOTE + " Median of active ads per day.")

    types = query("select distinct vehicle_type from daily_stats order by 1").vehicle_type.tolist()
    if not types:
        st.info("No daily stats yet. They're built after the first full sweep.")
        return
    c1, c2, c3, c4 = st.columns(4)
    vtype = c1.selectbox("Type", types, format_func=type_label,
                         index=types.index("car") if "car" in types else 0)
    makes = query("""select make from daily_stats where vehicle_type = %s
                     group by make order by sum(active_count) desc""", (vtype,)).make.tolist()
    make = c2.selectbox("Make", makes)
    models = query("""select model from daily_stats where vehicle_type = %s and make = %s
                      group by model order by sum(active_count) desc""", (vtype, make)).model.tolist()
    model = c3.selectbox("Model", models)
    years = query("""select year from daily_stats where vehicle_type = %s and make = %s and model = %s
                     group by year order by sum(active_count) desc""", (vtype, make, model)).year.tolist()
    year = c4.selectbox("Year", years, format_func=lambda y: "unknown" if y == 0 else str(y))
    if model is None or year is None:
        return

    series = query("""
        select day, active_count, new_count, delisted_count, median_price, min_price, max_price
        from daily_stats
        where vehicle_type = %s and make = %s and model = %s and year = %s
        order by day
    """, (vtype, make, model, year))
    name = f"{make} {model} {year or ''}".strip()

    if not series.empty:
        last = series.iloc[-1]
        m1, m2, m3 = st.columns(3)
        m1.metric("Median asking price", lkr(last.median_price))
        m2.metric("Range", f"{lkr(last.min_price)} – {lkr(last.max_price)}")
        m3.metric("Active ads", f"{int(last.active_count):,}")

        left, right = st.columns(2)
        left.markdown(f"**Median asking price — {name}**")
        fig = px.line(series, x="day", y="median_price", markers=len(series) < 40,
                      color_discrete_sequence=[SERIES_COLOR])
        fig.update_traces(hovertemplate="%{x|%b %d, %Y}: Rs %{y:,.0f}<extra></extra>")
        left.plotly_chart(_style_fig(fig, "Rs"), width="stretch")

        right.markdown(f"**Active ads — {name}**")
        fig = px.line(series, x="day", y="active_count", markers=len(series) < 40,
                      color_discrete_sequence=[SERIES_COLOR])
        fig.update_traces(hovertemplate="%{x|%b %d, %Y}: %{y:,} active<extra></extra>")
        right.plotly_chart(_style_fig(fig, "Ads"), width="stretch")

    st.subheader("Current ads")
    ads = query("""
        select title, price, mileage_km, condition, city, source, first_seen, url
        from listings
        where status = 'active' and vehicle_type = %s and make = %s and model = %s
          and coalesce(year, 0) = %s
        order by price nulls last
        limit 500
    """, (vtype, make, model, year))
    st.dataframe(ads, hide_index=True, width="stretch", column_config=_listing_columns())


def _listing_columns() -> dict[str, Any]:
    return {
        "price": st.column_config.NumberColumn("Price (Rs)", format="localized"),
        "mileage_km": st.column_config.NumberColumn("Mileage (km)", format="localized"),
        "first_seen": st.column_config.DatetimeColumn("Listed since", format="MMM D, YYYY"),
        "url": st.column_config.LinkColumn("Ad", display_text="open"),
    }


# --- Browse -------------------------------------------------------------------

def page_browse() -> None:
    st.title("Browse active listings")
    st.caption(ASKING_NOTE)

    types = query("select distinct vehicle_type from listings where status = 'active' order by 1").vehicle_type.tolist()
    c1, c2, c3, c4 = st.columns(4)
    vtype = c1.selectbox("Type", ["(any)", *types], format_func=lambda t: t if t == "(any)" else type_label(t))
    where = ["status = 'active'"]
    params: list[Any] = []
    if vtype != "(any)":
        where.append("vehicle_type = %s")
        params.append(vtype)

    makes = query(f"select make from listings where {' and '.join(where)} and make is not null "
                  "group by make order by count(*) desc", tuple(params)).make.tolist()
    make = c2.selectbox("Make", ["(any)", *makes])
    if make != "(any)":
        where.append("make = %s")
        params.append(make)

    models = query(f"select model from listings where {' and '.join(where)} and model is not null "
                   "group by model order by count(*) desc", tuple(params)).model.tolist()
    model = c3.selectbox("Model", ["(any)", *models])
    if model != "(any)":
        where.append("model = %s")
        params.append(model)

    source = c4.selectbox("Site", ["(any)", "riyasewana", "ikman"])
    if source != "(any)":
        where.append("source = %s")
        params.append(source)

    c5, c6 = st.columns(2)
    year_range = c5.slider("Year", 1970, pd.Timestamp.now().year + 1, (1990, pd.Timestamp.now().year + 1))
    where.append("year between %s and %s")
    params.extend(year_range)
    max_price_m = c6.slider("Max price (Rs millions)", 1, 200, 200)
    if max_price_m < 200:
        where.append("price <= %s")
        params.append(max_price_m * 1_000_000)

    ads = query(f"""
        select make, model, year, title, price, mileage_km, condition, city, source, first_seen, url
        from listings where {' and '.join(where)}
        order by last_seen desc
        limit 1000
    """, tuple(params))
    st.caption(f"{len(ads):,} ads shown (max 1,000, most recently seen first)")
    st.dataframe(ads, hide_index=True, width="stretch", column_config=_listing_columns())


PAGES = {"Market": page_market, "Model trend": page_model_trend, "Browse": page_browse}
choice = st.sidebar.radio("Page", list(PAGES))
st.sidebar.caption(
    "Data refreshes every 3 hours (new ads) and nightly (full sweep). "
    "Delisted usually means sold, sometimes expired."
)
PAGES[choice]()
