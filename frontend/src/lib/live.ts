"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

export interface LiveEvent {
  type: "claim.updated" | "claim.step";
  claim_id: number;
  status?: string;
  agent?: string;
  message?: string;
  at: string;
}

/** Subscribes to the backend's server-sent events and refreshes the affected queries.
 *  Events are only hints: the queries still poll slowly, so a dropped connection just
 *  means updates arrive a little later. */
export function useLiveEvents(onEvent?: (event: LiveEvent) => void) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    const source = new EventSource("/api/events");
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as LiveEvent;
      queryClient.invalidateQueries({ queryKey: ["claim", event.claim_id] });
      if (event.type === "claim.updated") {
        queryClient.invalidateQueries({ queryKey: ["claims"] });
        queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      }
      handler.current?.(event);
    };
    return () => source.close();
  }, [queryClient]);

  return connected;
}
