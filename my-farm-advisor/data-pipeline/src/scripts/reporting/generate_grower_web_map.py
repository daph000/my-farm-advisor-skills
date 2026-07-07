#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Generate a lightweight, self-contained interactive HTML web map for each farm under a grower.

Reads the farm's field_boundaries.geojson (actual pipeline output), embeds the polygons into a
single-page HTML file using Leaflet.js (CDN) with a CartoDB Positron basemap, and writes the
output to the farm's derived/dashboards/ directory.

Usage:
    python scripts/reporting/generate_grower_web_map.py --grower-slug northern-illinois-grower
    python scripts/reporting/generate_grower_web_map.py --grower-slug northern-illinois-grower --farm-slug northern-illinois-grower-illinois
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths  # noqa: E402

_RUNTIME_PATHS = resolve_runtime_paths()
_REPO = _RUNTIME_PATHS.runtime_base
_SCRIPTS = _RUNTIME_PATHS.runtime_scripts
_LIB = _RUNTIME_PATHS.runtime_scripts / "lib"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_LIB))

from paths import (  # noqa: E402
    farm_boundary_path,
    farm_dashboards_dir,
    farm_dir,
    grower_dir,
)

# Optional pipeline integration for idempotency manifests
try:
    from pipeline import (  # noqa: E402
        STEP_FARM_HTML_RENDER,
        FieldReportingConfig,
        build_step_manifest,
        load_manifest,
        step_is_stale,
    )

    _PIPELINE_AVAILABLE = True
except Exception:
    _PIPELINE_AVAILABLE = False

_DEFAULT_GROWER = os.environ.get("AG_GROWER_SLUG", "default-grower")
_DEFAULT_FARM = os.environ.get("AG_FARM_SLUG", "default-farm")
_DEFAULT_FARM_NAME = os.environ.get("AG_FARM_NAME", "Default Farm")


def _read_geojson(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _farm_name_from_json(grower_slug: str, farm_slug: str) -> str:
    farm_json_path = farm_dir(grower_slug, farm_slug) / "farm.json"
    if farm_json_path.exists():
        try:
            data = json.loads(farm_json_path.read_text(encoding="utf-8"))
            return data.get("display_name", farm_slug)
        except Exception:
            pass
    return farm_slug


def _generate_map_html(
    grower_slug: str,
    farm_slug: str,
    farm_name: str,
    geojson_data: dict[str, Any],
) -> str:
    """Build a standalone HTML page with an embedded Leaflet map."""

    # Sanitize strings for safe JS embedding
    def _js_str(value: str) -> str:
        return json.dumps(str(value))

    # Extract features and build a field list for the sidebar
    features = geojson_data.get("features", [])
    field_items: list[dict[str, Any]] = []
    for idx, feat in enumerate(features):
        props = feat.get("properties", {})
        fid = props.get("field_id", f"field-{idx}")
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [])
        # Compute a rough centroid for the sidebar zoom target
        centroid = _rough_centroid(coords)
        field_items.append(
            {
                "index": idx,
                "field_id": fid,
                "display_name": props.get("field_id", fid),
                "crop_name": props.get("crop_name", ""),
                "area_acres": props.get("area_acres", 0),
                "county_name": props.get("county_name", ""),
                "centroid": centroid,
            }
        )

    # Build the GeoJSON payload as a JS literal
    geojson_js = json.dumps(geojson_data, separators=(",", ":"))

    # Build sidebar list HTML
    sidebar_items_html = ""
    for item in field_items:
        lat, lng = item["centroid"]
        area_str = f"{float(item['area_acres']):.1f} ac" if item["area_acres"] else ""
        crop_str = item["crop_name"] if item["crop_name"] else ""
        meta = " — ".join(filter(None, [crop_str, area_str]))
        sidebar_items_html += textwrap.dedent(
            f"""\
            <li class="field-item" data-index="{item['index']}" data-lat="{lat}" data-lng="{lng}">
              <div class="field-name">{item['display_name']}</div>
              <div class="field-meta">{meta}</div>
            </li>
            """
        )

    if not sidebar_items_html:
        sidebar_items_html = '<li class="field-item"><div class="field-name">No fields found</div></li>'

    html = textwrap.dedent(
        f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{farm_name} — Field Map</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
          integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 0; height: 100vh; overflow: hidden; background: #f5f5f5; }}
    #app {{ display: flex; height: 100vh; width: 100vw; }}
    #sidebar {{ width: 280px; min-width: 280px; background: #ffffff; border-right: 1px solid #e0e0e0; display: flex; flex-direction: column; z-index: 1000; }}
    #sidebar-header {{ padding: 1rem 1.25rem; border-bottom: 1px solid #e0e0e0; background: #fafafa; }}
    #sidebar-header h1 {{ margin: 0; font-size: 1.1rem; color: #1a1a1a; }}
    #sidebar-header .subtitle {{ margin: 0.25rem 0 0; font-size: 0.8rem; color: #666; }}
    #field-list {{ list-style: none; margin: 0; padding: 0; overflow-y: auto; flex: 1; }}
    .field-item {{ padding: 0.75rem 1.25rem; border-bottom: 1px solid #f0f0f0; cursor: pointer; transition: background 0.15s; }}
    .field-item:hover {{ background: #eef6ff; }}
    .field-item.active {{ background: #dbeafe; border-left: 3px solid #2563eb; }}
    .field-name {{ font-weight: 600; font-size: 0.9rem; color: #1a1a1a; }}
    .field-meta {{ font-size: 0.78rem; color: #888; margin-top: 0.15rem; }}
    #map-container {{ flex: 1; position: relative; }}
    #map {{ height: 100%; width: 100%; }}
    .leaflet-popup-content-wrapper {{ border-radius: 8px; font-family: inherit; }}
    .popup-title {{ font-weight: 700; font-size: 1rem; margin-bottom: 0.4rem; color: #1a1a1a; }}
    .popup-row {{ font-size: 0.85rem; color: #444; margin: 0.2rem 0; }}
    .popup-row b {{ color: #1a1a1a; }}
    .toggle-btn {{ display: none; position: absolute; top: 10px; left: 10px; z-index: 1001; background: white; border: 1px solid #ccc; border-radius: 4px; padding: 6px 10px; font-size: 0.85rem; cursor: pointer; box-shadow: 0 2px 6px rgba(0,0,0,0.15); }}
    @media (max-width: 768px) {{
      #sidebar {{ position: absolute; left: 0; top: 0; bottom: 0; transform: translateX(-100%); transition: transform 0.25s; }}
      #sidebar.open {{ transform: translateX(0); }}
      .toggle-btn {{ display: block; }}
    }}
  </style>
</head>
<body>
  <div id="app">
    <button class="toggle-btn" id="toggleSidebar" onclick="document.getElementById('sidebar').classList.toggle('open')">☰ Fields</button>
    <aside id="sidebar">
      <div id="sidebar-header">
        <h1>{farm_name}</h1>
        <div class="subtitle">{len(field_items)} fields &middot; Grower: {grower_slug}</div>
      </div>
      <ul id="field-list">
        {sidebar_items_html}
      </ul>
    </aside>
    <div id="map-container">
      <div id="map"></div>
    </div>
  </div>

  <script>
(function() {{
  'use strict';

  const geojsonData = {geojson_js};

  const map = L.map('map', {{ zoomControl: false }}).setView([41.0, -93.0], 5);
  L.control.scale({{ imperial: true, metric: true }}).addTo(map);
  L.control.zoom({{ position: 'topright' }}).addTo(map);

  // Esri satellite imagery base
  L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}',
    {{
      attribution: 'Imagery &copy; <a href="https://www.esri.com">Esri</a>',
      maxZoom: 19
    }}
  ).addTo(map);

  const layerStyle = {{
    color: '#166534',
    weight: 2,
    opacity: 0.9,
    fillColor: '#22c55e',
    fillOpacity: 0.35
  }};

  const hoverStyle = {{
    color: '#1e40af',
    weight: 3,
    opacity: 1,
    fillColor: '#60a5fa',
    fillOpacity: 0.5
  }};

  const geojsonLayer = L.geoJSON(geojsonData, {{
    style: function() {{ return layerStyle; }},
    onEachFeature: function(feature, layer) {{
      const props = feature.properties || {{}};
      const fid = props.field_id || 'Unknown';
      const area = props.area_acres != null ? Number(props.area_acres).toFixed(1) + ' ac' : '—';
      const crop = props.crop_name || '—';
      const county = props.county_name || '—';
      const popupHtml = `
        <div class="popup-title">${{fid}}</div>
        <div class="popup-row"><b>Grower:</b> {_js_str(grower_slug)}</div>
        <div class="popup-row"><b>Farm:</b> {_js_str(farm_name)}</div>
        <div class="popup-row"><b>Field:</b> ${{fid}}</div>
        <div class="popup-row"><b>Area:</b> ${{area}}</div>
        <div class="popup-row"><b>Crop:</b> ${{crop}}</div>
        <div class="popup-row"><b>County:</b> ${{county}}</div>
      `;
      layer.bindPopup(popupHtml);

      layer.on('mouseover', function() {{
        layer.setStyle(hoverStyle);
        if (!L.Browser.ie && !L.Browser.opera && !L.Browser.edge) {{
          layer.bringToFront();
        }}
      }});
      layer.on('mouseout', function() {{
        layer.setStyle(layerStyle);
      }});
      layer.on('click', function() {{
        layer.openPopup();
        // Highlight corresponding sidebar item
        document.querySelectorAll('.field-item').forEach(function(el) {{
          el.classList.remove('active');
        }});
        const idx = geojsonData.features.indexOf(feature);
        const item = document.querySelector('.field-item[data-index="' + idx + '"]');
        if (item) {{
          item.classList.add('active');
          item.scrollIntoView({{ block: 'nearest', behavior: 'smooth' }});
        }}
      }});
    }}
  }}).addTo(map);

  // Esri reference/labels overlay — kept on top so roads and place names stay readable
  L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{{z}}/{{y}}/{{x}}',
    {{
      attribution: 'Labels &copy; <a href="https://www.esri.com">Esri</a>',
      maxZoom: 19
    }}
  ).addTo(map);

  if (geojsonLayer.getBounds().isValid()) {{
    map.fitBounds(geojsonLayer.getBounds().pad(0.1));
  }}

  // Sidebar zoom-to behaviour
  document.querySelectorAll('.field-item').forEach(function(item) {{
    item.addEventListener('click', function() {{
      const idx = parseInt(this.getAttribute('data-index'), 10);
      const lat = parseFloat(this.getAttribute('data-lat'));
      const lng = parseFloat(this.getAttribute('data-lng'));
      const feature = geojsonData.features[idx];
      if (!feature) return;

      // Find the corresponding Leaflet layer
      geojsonLayer.eachLayer(function(layer) {{
        if (layer.feature === feature) {{
          layer.openPopup();
          layer.setStyle(hoverStyle);
          setTimeout(function() {{ layer.setStyle(layerStyle); }}, 2000);
        }}
      }});

      if (!isNaN(lat) && !isNaN(lng)) {{
        map.flyTo([lat, lng], 15, {{ duration: 1.2 }});
      }}

      document.querySelectorAll('.field-item').forEach(function(el) {{ el.classList.remove('active'); }});
      this.classList.add('active');
    }});
  }});
}})();
  </script>
</body>
</html>
"""
    )
    return html


def _rough_centroid(coords: Any) -> tuple[float, float]:
    """Compute a rough centroid from GeoJSON Polygon coordinates for fly-to targets."""
    # coords is typically [ [ [lon, lat], ... ] ] for a Polygon
    if not coords or not isinstance(coords, list):
        return (0.0, 0.0)
    # Handle Polygon (list of rings)
    ring = coords[0] if isinstance(coords[0], list) and len(coords) > 0 else coords
    if not ring or not isinstance(ring, list):
        return (0.0, 0.0)
    lats: list[float] = []
    lngs: list[float] = []
    for pt in ring:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                lngs.append(float(pt[0]))
                lats.append(float(pt[1]))
            except (ValueError, TypeError):
                continue
    if not lats:
        return (0.0, 0.0)
    return (sum(lats) / len(lats), sum(lngs) / len(lngs))


def _process_farm(grower_slug: str, farm_slug: str, force: bool = False) -> Path | None:
    boundary_path = farm_boundary_path(grower_slug, farm_slug)
    geojson_data = _read_geojson(boundary_path)
    if geojson_data is None:
        print(f"  ! No boundary file for farm {farm_slug}; skipping.")
        return None

    farm_name = _farm_name_from_json(grower_slug, farm_slug)
    output_dir = farm_dashboards_dir(grower_slug, farm_slug)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{farm_slug}_web_map.html"

    # Idempotency check using pipeline manifest if available
    if _PIPELINE_AVAILABLE and not force:
        manifest_dir = farm_dir(grower_slug, farm_slug) / "manifests"
        prior = load_manifest(manifest_dir / "grower_web_map.json")
        config = FieldReportingConfig(
            farm_name=farm_name,
            field_boundary_path=str(boundary_path.relative_to(_REPO)),
            grower_slug=grower_slug,
            farm_slug=farm_slug,
        )
        manifest = build_step_manifest(
            step_name="grower_web_map",
            input_paths=[str(boundary_path.relative_to(_REPO))],
            output_paths=[output_path],
            code_paths=[Path(__file__)],
            config=config,
        )
        if not step_is_stale(manifest, prior):
            print(f"  skip  web map (current) → {output_path}")
            return output_path

    html = _generate_map_html(grower_slug, farm_slug, farm_name, geojson_data)
    output_path.write_text(html, encoding="utf-8")
    size_kb = output_path.stat().st_size / 1024
    print(f"  ✓ web map saved → {output_path} ({size_kb:.1f} KB)")

    if _PIPELINE_AVAILABLE:
        manifest_dir = farm_dir(grower_slug, farm_slug) / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        config = FieldReportingConfig(
            farm_name=farm_name,
            field_boundary_path=str(boundary_path.relative_to(_REPO)),
            grower_slug=grower_slug,
            farm_slug=farm_slug,
        )
        manifest = build_step_manifest(
            step_name="grower_web_map",
            input_paths=[str(boundary_path.relative_to(_REPO))],
            output_paths=[output_path],
            code_paths=[Path(__file__)],
            config=config,
        )
        manifest.status = "complete"
        manifest.write(manifest_dir / "grower_web_map.json")

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a lightweight interactive HTML web map for a grower's farms."
    )
    parser.add_argument(
        "--grower-slug",
        required=True,
        help="Grower slug (e.g. northern-illinois-grower).",
    )
    parser.add_argument(
        "--farm-slug",
        default="",
        help="Optional farm slug. If omitted, all farms under the grower are processed.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate even if the output appears current.",
    )
    args = parser.parse_args()

    grower_slug = args.grower_slug
    farm_slug = args.farm_slug
    force = args.force

    print("=" * 60)
    print("Grower Web Map — interactive HTML map generator")
    print("=" * 60)

    farms_to_process: list[str] = []
    if farm_slug:
        farms_to_process = [farm_slug]
    else:
        grower_path = grower_dir(grower_slug)
        farms_root = grower_path / "farms"
        if farms_root.exists():
            farms_to_process = sorted([p.name for p in farms_root.iterdir() if p.is_dir()])
        if not farms_to_process:
            print(f"! No farms found for grower {grower_slug}")
            sys.exit(1)

    print(f"Grower: {grower_slug}")
    print(f"Farms to process: {len(farms_to_process)}")
    generated: list[Path] = []
    for slug in farms_to_process:
        print(f"\n  Farm: {slug}")
        result = _process_farm(grower_slug, slug, force=force)
        if result:
            generated.append(result)

    print("\n" + "=" * 60)
    print(f"Done. Generated {len(generated)} map(s).")
    for p in generated:
        print(f"  → {p}")
    print("=" * 60)


if __name__ == "__main__":
    main()
