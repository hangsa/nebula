import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent } from "@testing-library/react";
import "@testing-library/jest-dom";
import MapStep from "./MapStep";
import type { MapPayload } from "../../api/client";
import { WizardProvider } from "./WizardContext";

// Mock the api so MapStep doesn't try to hit the network. We pre-seed
// wizard.data.map via sessionStorage so the component hydrates synchronously
// and skips the getMap() effect.
vi.mock("../../api/client", () => ({
  default: {
    getMap: vi.fn().mockResolvedValue({}),
    generateMap: vi.fn().mockResolvedValue({}),
    updateMap: vi.fn().mockResolvedValue(undefined),
    patchMapLocation: vi.fn(),
    deleteMapLocation: vi.fn().mockResolvedValue(undefined),
    patchMapRegion: vi.fn(),
    addMapRegion: vi.fn(),
    addMapRoute: vi.fn(),
    deleteMapRoute: vi.fn().mockResolvedValue({ deleted_id: "x" }),
    addMapPoi: vi.fn(),
    deleteMapPoi: vi.fn().mockResolvedValue({ deleted_id: "x" }),
    patchMapSettings: vi.fn(),
    getMapSnapshots: vi.fn().mockResolvedValue([]),
    rollbackMap: vi.fn(),
  },
}));

const SAMPLE_MAP: MapPayload = {
  schema_version: "1.0",
  project_id: "proj_test",
  regions: [
    {
      id: "region_south",
      name: "南泽",
      aliases: [],
      level: "state",
      parent_id: null,
      climate: "",
      tags: [],
      controlled_by: [],
      adjacent_region_ids: [],
      display_pos: null,
    },
  ],
  locations: [
    {
      id: "loc_north",
      name: "北门",
      aliases: [],
      type: "city",
      region_id: "region_south",
      pos_hint: "",
      tags: [],
      factions: [],
      enter_conditions: [],
      secrets: [],
      dramatic_role: { wanted_by: [], decisions_unlocked: [], departure_cost: "" },
      space_type: "world",
      display_pos: null,
    },
    {
      id: "loc_south",
      name: "南门",
      aliases: [],
      type: "inn",
      region_id: "region_south",
      pos_hint: "",
      tags: [],
      factions: [],
      enter_conditions: [],
      secrets: [],
      dramatic_role: { wanted_by: [], decisions_unlocked: [], departure_cost: "" },
      space_type: "world",
      display_pos: null,
    },
  ],
  routes: [
    {
      id: "route_bidi",
      from: "loc_north",
      to: "loc_south",
      bidirectional: true,
      kind: "waterway",
      distance_tier: "intra_city",
      est_travel_minutes: 15,
      risk: "high",
      conditions: [],
      encounters: [],
      accessible: true,
    },
    {
      id: "route_oneway",
      from: "loc_south",
      to: "loc_north",
      bidirectional: false,
      kind: "road",
      distance_tier: "inter_city",
      est_travel_minutes: 40,
      risk: "low",
      conditions: [],
      encounters: [],
      accessible: true,
    },
  ],
  pois: [
    {
      id: "poi_shrine",
      name: "古神龛",
      parent_location_id: "loc_north",
      kind: "shrine",
      description: "",
      discoverable: true,
      first_discovered_chapter: null,
      tags: [],
    },
    {
      id: "poi_cache",
      name: "埋藏点",
      parent_location_id: "loc_south",
      kind: "cache",
      description: "",
      discoverable: true,
      first_discovered_chapter: null,
      tags: [],
    },
  ],
  location_states: [],
  snapshots: [],
  footprints: [],
  assertions: [],
  change_log: [],
  display: { positions: {} },
  settings: {
    mode: "allow_alias_new",
    scope_enabled: false,
    allowed_region_ids: [],
    chapter_new_location_cap: 5,
    reuse_rate_target: 0.6,
    strict_geo: false,
  },
};

function mountWithMap(initialMap: MapPayload = SAMPLE_MAP) {
  sessionStorage.setItem(
    "nebula.wizard.state.proj_test",
    JSON.stringify({
      currentStep: 4,
      completedSteps: [1, 2, 3],
      status: "completed",
      data: { map: initialMap },
    }),
  );
  return render(
    <WizardProvider projectId="proj_test">
      <MapStep projectId="proj_test" />
    </WizardProvider>,
  );
}

function clickTab(label: string) {
  // SubTabStrip renders role="tab" buttons — query by role.
  fireEvent.click(screen.getByRole("tab", { name: label }));
}

beforeEach(() => {
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("MapStep — Routes tab Chinese labels", () => {
  it("test_routes_panel_shows_location_names_not_ids", () => {
    mountWithMap();
    clickTab("路线");
    const list = screen.getByTestId("routes-list");
    const bidiRow = within(list).getByTestId("route-row-route_bidi");
    // The Chinese names must appear in the route row.
    expect(bidiRow).toHaveTextContent("北门");
    expect(bidiRow).toHaveTextContent("南门");
    // The raw ID strings must NOT appear in the rendered route-row content.
    expect(bidiRow.textContent).not.toMatch(/loc_north/);
    expect(bidiRow.textContent).not.toMatch(/loc_south/);
  });

  it("test_routes_panel_shows_chinese_kind_and_distance_tier", () => {
    mountWithMap();
    clickTab("路线");
    const list = screen.getByTestId("routes-list");
    const row = within(list).getByTestId("route-row-route_bidi");
    // Enum → Chinese labels
    expect(row).toHaveTextContent("水路");      // kind: waterway
    expect(row).toHaveTextContent("城内");      // distance_tier: intra_city
    expect(row).toHaveTextContent("15 分钟");   // est_travel_minutes: 15
    expect(row).toHaveTextContent("高风险");    // risk: high
    // English enum strings must not leak into the row.
    expect(row.textContent).not.toMatch(/waterway/);
    expect(row.textContent).not.toMatch(/intra_city/);
    expect(row.textContent).not.toMatch(/\bhigh\b/);
  });

  it("test_routes_panel_shows_bidirectional_arrow_correctly", () => {
    mountWithMap();
    clickTab("路线");
    const list = screen.getByTestId("routes-list");
    const bidiRow = within(list).getByTestId("route-row-route_bidi");
    const oneRow = within(list).getByTestId("route-row-route_oneway");
    // Bidirectional → ⇄ (and NOT →)
    expect(bidiRow).toHaveTextContent("⇄");
    expect(bidiRow.textContent).not.toMatch(/→/);
    // One-way → → (and NOT ⇄)
    expect(oneRow).toHaveTextContent("→");
    expect(oneRow.textContent).not.toMatch(/⇄/);
  });
});

describe("MapStep — Locations tab Chinese labels", () => {
  it("test_locations_panel_shows_chinese_type_label", () => {
    mountWithMap();
    // Locations is the default tab.
    const list = screen.getByTestId("locations-list");
    const northRow = within(list).getByTestId("location-row-北门");
    const southRow = within(list).getByTestId("location-row-南门");
    expect(northRow).toHaveTextContent("城镇");  // type: city
    expect(southRow).toHaveTextContent("客栈");  // type: inn
    expect(northRow.textContent).not.toMatch(/\bcity\b/);
    expect(southRow.textContent).not.toMatch(/\binn\b/);
  });

  it("test_locations_panel_resolves_region_id_to_region_name", () => {
    mountWithMap();
    const list = screen.getByTestId("locations-list");
    const northRow = within(list).getByTestId("location-row-北门");
    // region_id="region_south" → name="南泽"
    expect(northRow).toHaveTextContent("南泽");
    // The raw region ID must NOT appear in the user-visible row text.
    expect(northRow.textContent).not.toMatch(/region_south/);
  });
});

describe("MapStep — Regions tab Chinese labels", () => {
  it("test_regions_panel_shows_chinese_level", () => {
    mountWithMap();
    clickTab("区域");
    const list = screen.getByTestId("regions-list");
    // region_south has level="state" → "州/省"
    const rows = list.querySelectorAll("li");
    expect(rows.length).toBeGreaterThan(0);
    const row = rows[0];
    expect(row).toHaveTextContent("州/省");
    // The raw English level string must NOT appear in the row.
    expect(row?.textContent).not.toMatch(/\bstate\b/);
  });
});

describe("MapStep — POIs tab Chinese labels", () => {
  it("test_pois_panel_shows_chinese_kind", () => {
    mountWithMap();
    clickTab("POI");
    const list = screen.getByTestId("pois-list");
    const shrineRow = within(list).getByTestId("poi-row-poi_shrine");
    const cacheRow = within(list).getByTestId("poi-row-poi_cache");
    expect(shrineRow).toHaveTextContent("神龛");    // kind: shrine
    expect(cacheRow).toHaveTextContent("藏匿点");  // kind: cache
    expect(shrineRow.textContent).not.toMatch(/\bshrine\b/);
    expect(cacheRow.textContent).not.toMatch(/\bcache\b/);
  });
});