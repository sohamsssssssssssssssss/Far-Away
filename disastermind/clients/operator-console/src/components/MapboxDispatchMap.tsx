// Mapbox GL dispatch map — plots dispatches / field orders on a dark vector map.
//
// Replaces the Leaflet-based DispatchMap. Uses react-map-gl (v7) with:
//   - GeoJSON Source + Layer for route polylines
//   - Custom Markers with priority-based coloring
//   - Click-to-reveal Popup with order details
//   - India-centred dark-v11 style; degrades to a dark canvas offline
//
// Token comes from VITE_MAPBOX_TOKEN. Without it the map still renders markers
// on a blank dark canvas (no tile requests).

import { useCallback, useMemo, useState } from "react";
import type { Feature, FeatureCollection, LineString } from "geojson";
import Map, {
  Layer,
  Marker,
  Popup,
  Source,
  type ViewStateChangeEvent,
} from "react-map-gl/mapbox";

import type { Message } from "../api/types";
import { isEscalationish } from "../api/types";
import { orderAnchor, ordersFromMessages, type PlottedOrder } from "../lib/geo";

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined;

// India-centred default view.
const INITIAL_VIEW = {
  longitude: 79.0,
  latitude: 22.0,
  zoom: 4.5,
  pitch: 0,
  bearing: 0,
};

const MAP_STYLE = "mapbox://styles/mapbox/dark-v11";

// No-token fallback: an empty dark style so the map container still renders
// markers on a plain dark canvas without requesting Mapbox tiles.
// Minimal Mapbox style spec for offline/no-token fallback.
const FALLBACK_STYLE = {
  version: 8 as const,
  name: "blank-dark",
  sources: {} as Record<string, never>,
  layers: [
    {
      id: "background",
      type: "background" as const,
      paint: { "background-color": "#10212e" },
    },
  ],
};

function color(priority: number): string {
  if (priority <= 1) return "#f85149"; // CRITICAL
  if (priority === 2) return "#d29922"; // HIGH
  return "#58a6ff";
}

function isDispatchTopic(topic: string): boolean {
  return /dispatch|field_order|routing_plan/i.test(topic) || isEscalationish(topic);
}

/** Convert route waypoints to a GeoJSON FeatureCollection of LineStrings. */
function routeGeoJson(
  orders: PlottedOrder[],
): FeatureCollection<LineString> {
  const features: Feature<LineString>[] = [];
  for (const o of orders) {
    if (o.waypoints.length < 2) continue;
    features.push({
      type: "Feature",
      properties: { priority: o.priority, key: o.key },
      geometry: {
        type: "LineString",
        coordinates: o.waypoints.map((w) => [w.lon, w.lat]),
      },
    });
  }
  return { type: "FeatureCollection", features };
}

/** A single pulsing marker dot for a dispatch destination. */
function OrderMarker({
  order,
  onClick,
}: {
  order: PlottedOrder;
  onClick: (o: PlottedOrder) => void;
}) {
  const dest = orderAnchor(order);
  const c = color(order.priority);
  return (
    <Marker
      longitude={dest.lon}
      latitude={dest.lat}
      anchor="center"
      onClick={(e) => {
        e.originalEvent?.stopPropagation();
        onClick(order);
      }}
    >
      <div
        className="dispatch-marker"
        style={{
          "--marker-color": c,
        } as React.CSSProperties}
        title={order.site ?? order.topic}
      >
        <div className="dispatch-marker-ring" />
        <div className="dispatch-marker-dot" />
      </div>
    </Marker>
  );
}

/** Intermediate waypoint dots (smaller, no interaction). */
function WaypointMarkers({ order }: { order: PlottedOrder }) {
  if (order.waypoints.length < 2) return null;
  const c = color(order.priority);
  // Skip the last waypoint — that's the destination (handled by OrderMarker).
  return (
    <>
      {order.waypoints.slice(0, -1).map((w, i) => (
        <Marker
          key={`${order.key}:wp:${i}`}
          longitude={w.lon}
          latitude={w.lat}
          anchor="center"
        >
          <div
            className="waypoint-dot"
            style={{ background: c, boxShadow: `0 0 4px ${c}` }}
          />
        </Marker>
      ))}
    </>
  );
}

/** Order info popup. */
function OrderPopup({
  order,
  onClose,
}: {
  order: PlottedOrder;
  onClose: () => void;
}) {
  const dest = orderAnchor(order);
  return (
    <Popup
      longitude={dest.lon}
      latitude={dest.lat}
      anchor="bottom"
      onClose={onClose}
      closeOnClick={false}
      className="dispatch-popup"
    >
      <div className="dispatch-popup-content">
        <div className="dispatch-popup-title">
          {order.site ?? order.topic}
        </div>
        <div className="dispatch-popup-meta">
          <span
            className="dispatch-popup-priority"
            style={{ color: color(order.priority) }}
          >
            {order.priorityLabel}
          </span>
          <span className="dispatch-popup-topic">{order.topic}</span>
        </div>
        {order.team ? (
          <div className="dispatch-popup-detail">Team {order.team}</div>
        ) : null}
        {order.incidentId ? (
          <div className="dispatch-popup-detail">{order.incidentId}</div>
        ) : null}
        {order.reason ? (
          <div className="dispatch-popup-reason">{order.reason}</div>
        ) : null}
      </div>
    </Popup>
  );
}

export function MapboxDispatchMap({ messages }: { messages: Message[] }) {
  const [viewState, setViewState] = useState(INITIAL_VIEW);
  const [selectedOrder, setSelectedOrder] = useState<PlottedOrder | null>(null);

  const dispatchMsgs = useMemo(
    () => messages.filter((m) => isDispatchTopic(m.topic)),
    [messages],
  );
  const orders = useMemo(
    () => ordersFromMessages(dispatchMsgs),
    [dispatchMsgs],
  );
  const routes = useMemo(() => routeGeoJson(orders), [orders]);

  const handleMove = useCallback(
    (evt: ViewStateChangeEvent) => setViewState(evt.viewState),
    [],
  );
  const handleClick = useCallback((o: PlottedOrder) => setSelectedOrder(o), []);
  const handlePopupClose = useCallback(() => setSelectedOrder(null), []);

  return (
    <section className="panel full" id="dispatch-map-section">
      <h2>
        Dispatch map <span className="count">{orders.length}</span>
      </h2>
      <div className="map-wrap">
        <Map
          {...viewState}
          onMove={handleMove}
          mapboxAccessToken={MAPBOX_TOKEN || undefined}
          mapStyle={MAPBOX_TOKEN ? MAP_STYLE : FALLBACK_STYLE}
          style={{ width: "100%", height: "100%" }}
          attributionControl={true}
          reuseMaps
        >
          {/* Route polylines */}
          <Source id="routes" type="geojson" data={routes}>
            <Layer
              id="route-lines"
              type="line"
              paint={{
                "line-color": [
                  "match",
                  ["get", "priority"],
                  1, "#f85149",
                  2, "#d29922",
                  /* default */ "#58a6ff",
                ],
                "line-width": 2.5,
                "line-opacity": 0.7,
              }}
              layout={{
                "line-cap": "round",
                "line-join": "round",
              }}
            />
          </Source>

          {/* Intermediate waypoint dots */}
          {orders.map((o) => (
            <WaypointMarkers key={`wps:${o.key}`} order={o} />
          ))}

          {/* Destination markers */}
          {orders.map((o) => (
            <OrderMarker key={o.key} order={o} onClick={handleClick} />
          ))}

          {/* Selected order popup */}
          {selectedOrder ? (
            <OrderPopup order={selectedOrder} onClose={handlePopupClose} />
          ) : null}
        </Map>
      </div>
      <div className="map-legend">
        <span>
          <span className="sw" style={{ background: "#f85149" }} />
          CRITICAL
        </span>
        <span>
          <span className="sw" style={{ background: "#d29922" }} />
          HIGH
        </span>
        <span>
          <span className="sw" style={{ background: "#58a6ff" }} />
          other · pulsing dot = destination
        </span>
        {orders.length === 0 ? (
          <span className="empty">no geo-tagged dispatches yet</span>
        ) : null}
      </div>
    </section>
  );
}
