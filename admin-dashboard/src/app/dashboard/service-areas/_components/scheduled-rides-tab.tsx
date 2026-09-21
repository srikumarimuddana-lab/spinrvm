"use client";

import { useId, useState } from "react";
import { updateServiceArea } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

type Config = { enabled: boolean; dispatch_lead_minutes: number; driver_reminder_minutes: number; rider_reminder_minutes: number };
const defaults: Config = { enabled: false, dispatch_lead_minutes: 10, driver_reminder_minutes: 10, rider_reminder_minutes: 10 };
const fields = [
  { key: "dispatch_lead_minutes", label: "Start matching before pickup (minutes)", min: 0, max: 30 },
  { key: "driver_reminder_minutes", label: "Driver reminder before pickup (minutes)", min: 1, max: 60 },
  { key: "rider_reminder_minutes", label: "Rider reminder before pickup (minutes)", min: 1, max: 60 },
] as const;

export default function ScheduledRidesTab({ area, onSaved }: {
  area: { id: string; name: string; scheduled_ride_config?: Partial<Config> };
  onSaved: (config: Config) => void;
}) {
  const id = useId();
  const initial = { ...defaults, ...area.scheduled_ride_config };
  const [enabled, setEnabled] = useState(initial.enabled);
  const [values, setValues] = useState({ dispatch_lead_minutes: String(initial.dispatch_lead_minutes),
    driver_reminder_minutes: String(initial.driver_reminder_minutes), rider_reminder_minutes: String(initial.rider_reminder_minutes) });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  async function save() {
    setSaved(false);
    if (fields.some(({key, min, max}) => values[key].trim() === "" || !Number.isInteger(Number(values[key])) || Number(values[key]) < min || Number(values[key]) > max)) {
      setError("Enter whole minutes: matching 0–30; driver and rider reminders 1–60.");
      return;
    }
    const config: Config = { enabled, dispatch_lead_minutes: Number(values.dispatch_lead_minutes),
      driver_reminder_minutes: Number(values.driver_reminder_minutes), rider_reminder_minutes: Number(values.rider_reminder_minutes) };
    setSaving(true);
    setError("");
    try {
      await updateServiceArea(area.id, { scheduled_ride_config: config });
      onSaved(config);
      setSaved(true);
    } catch {
      setError("Could not save scheduled ride settings. Your changes are still here; please try again.");
    } finally { setSaving(false); }
  }

  return <section className="space-y-5" aria-label={`Scheduled rides in ${area.name}`}>
    <div><h3 className="text-base font-semibold">Scheduled rides · {area.name}</h3>
      <p className="mt-1 text-sm text-muted-foreground">Set when drivers receive offers and when pickup reminders are sent.</p></div>
    <div className="flex items-center gap-3">
      <input id={`${id}-enabled`} type="checkbox" checked={enabled} disabled={saving}
        onChange={event => { setEnabled(event.target.checked); setSaved(false); }} />
      <Label htmlFor={`${id}-enabled`}>Use these scheduled ride settings</Label>
    </div>
    <p className="text-sm text-muted-foreground">When off, matching starts at pickup time and riders receive the standard 10-minute reminder.
      The global Scheduled dispatch switch must also be on.</p>
    <div className="grid gap-4 sm:grid-cols-3">
      {fields.map(({key, label, min, max}) => <div key={key} className="space-y-2">
        <Label htmlFor={`${id}-${key}`}>{label}</Label>
        <Input id={`${id}-${key}`} type="number" min={min} max={max} step={1} value={values[key]} disabled={saving}
          onChange={event => { setValues({...values, [key]: event.target.value}); setSaved(false); }} />
      </div>)}
    </div>
    <p className="text-sm text-muted-foreground">Driver reminders go to the driver who has accepted the ride. If acceptance happens after the reminder time but before pickup,
      the reminder follows on the next check. Start matching earlier than the driver reminder to allow time for acceptance.</p>
    <p className="text-sm text-muted-foreground">Changes affect upcoming bookings on the next check, about once a minute.
      Already-sent reminders are not repeated. Driver availability and notification delivery can affect timing; pickup is not guaranteed.</p>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {saved && <p role="status" className="text-sm">Scheduled ride settings saved.</p>}
    <Button type="button" disabled={saving} onClick={save}>{saving ? "Saving…" : "Save scheduled ride settings"}</Button>
  </section>;
}
