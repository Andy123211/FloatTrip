/** Public API shapes follow app/api/runtime_routes.py and its Pydantic models. */
export type RunStatus =
  "queued" | "running" | "waiting_user" | "succeeded" | "failed" | "cancelled";
export type TripFocus = "sights_first" | "food_first" | "balanced";

export interface RunCreateRequest {
  kind: "travel_plan";
  request: {
    query: string;
    destination: string;
    start_date: string;
    end_date: string;
    days: number;
    trip_focus: TripFocus;
    attraction_preference?: string;
    food_preference?: string;
    habit_preference?: string;
  };
}

export interface RunPublic {
  id: string;
  kind: string;
  status: RunStatus;
  result_itinerary_id?: string | null;
  error_public?: {
    code?: string;
    message?: string;
    retryable?: boolean;
  } | null;
}

export interface RuntimeEvent {
  sequence: number;
  kind: "messages" | "custom" | "error" | "heartbeat" | "end";
  payload: Record<string, unknown>;
}

export interface ItineraryItem {
  type?: string;
  name?: string;
  start_time?: string;
  end_time?: string;
  period?: string;
  address?: string;
  tip?: string;
  dist_from_prev_km?: number;
  location?: { lat?: number; lng?: number };
}

export interface ItineraryDay {
  day: number;
  date?: string;
  theme?: string;
  timeline: ItineraryItem[];
}

export interface ItineraryPlan {
  destination?: string;
  start_date?: string;
  end_date?: string;
  days_count?: number;
  days: ItineraryDay[];
  validation_summary?: { status?: string; notes?: string[] };
  solver_diagnostics?: {
    status?: string;
    elapsed_ms?: number;
    fallback_used?: boolean;
  } | null;
  route_issues?: string[];
  weather_note?: string | null;
}

export interface ItineraryEnvelope {
  id: string;
  plan: ItineraryPlan;
}
