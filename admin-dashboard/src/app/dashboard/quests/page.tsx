"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Trophy, Plus, Users,
  RefreshCw, ChevronDown, ChevronUp, BarChart3,
} from "lucide-react";
import { getQuests, createQuest, updateQuest, getQuestParticipants, getServiceAreas } from "@/lib/api";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";

// datetime-local inputs hold a *local* wall-clock string. Format a Date into
// that shape in LOCAL time — using toISOString() here would stuff a UTC clock
// into a local-interpreted field, so on submit new Date(value).toISOString()
// would double-convert and push the quest's start hours into the future
// (Saskatchewan is UTC−6), hiding it from drivers until then.
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const ALL_AREAS = "__all__";

interface Quest {
  id: string;
  title: string;
  description: string;
  type: string;
  target_value: number;
  reward_amount: number;
  reward_type: string;
  start_date: string;
  end_date: string;
  is_active: boolean;
  max_participants: number | null;
  service_area_id: string | null;
  min_driver_rating: number | null;
  stats?: {
    total_participants: number;
    completed: number;
    claimed: number;
  };
  created_at: string;
}

interface Participant {
  progress_id: string;
  driver_id: string;
  driver_name: string;
  current_value: number;
  target_value: number;
  progress_pct: number;
  status: string;
  started_at: string;
  completed_at: string | null;
  claimed_at: string | null;
}

// All quest types, used for rendering a human label wherever a quest's type is
// shown (table, existing quests of any type).
const QUEST_TYPES = [
  { value: "ride_count", label: "Ride Count" },
  { value: "earnings_target", label: "Earnings Target" },
  { value: "online_hours", label: "Online Hours" },
  { value: "peak_rides", label: "Peak Hour Rides" },
  { value: "consecutive_days", label: "Consecutive Days" },
  { value: "rating_maintained", label: "Rating Maintained" },
];

// Only these types have progression logic wired in the backend
// (utils/quest_tracker.py advances them on ride completion). The others
// (online_hours, consecutive_days, rating_maintained) need dedicated trackers —
// online-session time aggregation, timezone-aware day-streak counting, and an
// end-of-window rating evaluator respectively — so a quest created with them
// would silently never progress. Keep them out of the create dropdown until
// those trackers exist; QUEST_TYPES above still renders their labels for any
// pre-existing quests.
const CREATABLE_QUEST_TYPES = QUEST_TYPES.filter((t) =>
  ["ride_count", "earnings_target", "peak_rides"].includes(t.value),
);

const STATUS_COLORS: Record<string, string> = {
  // eslint-disable-next-line no-restricted-syntax -- distinct quest lifecycle stages that must stay visually distinguishable; collapsing to success/warning tokens would make "completed" and "claimed" indistinguishable (#2816)
  active: "bg-blue-100 text-blue-700",
  // eslint-disable-next-line no-restricted-syntax -- see above
  completed: "bg-green-100 text-green-700",
  // eslint-disable-next-line no-restricted-syntax -- see above
  claimed: "bg-amber-100 text-amber-700",
  expired: "bg-muted text-muted-foreground",
};

export default function QuestsPage() {
  const [quests, setQuests] = useState<Quest[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [expandedQuest, setExpandedQuest] = useState<string | null>(null);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [participantsLoading, setParticipantsLoading] = useState(false);
  const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);

  const { sorted: sortedQuests, sort: questSort, toggle: toggleQuestSort } = useTableSort(quests);
  const { sorted: sortedParticipants, sort: participantSort, toggle: toggleParticipantSort } = useTableSort(participants);

  // Form state
  const [form, setForm] = useState({
    title: "",
    description: "",
    type: "ride_count",
    target_value: 20,
    reward_amount: 25,
    reward_type: "wallet_credit",
    start_date: toLocalInput(new Date()),
    end_date: toLocalInput(new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)),
    max_participants: "",
    min_driver_rating: "",
    service_area_id: ALL_AREAS,
  });

  const fetchQuests = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getQuests();
      setQuests(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Failed to fetch quests:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchQuests(); }, [fetchQuests]);

  useEffect(() => {
    getServiceAreas()
      .then((areas) => setServiceAreas((Array.isArray(areas) ? areas : []).map((a: { id: string; name: string }) => ({ id: a.id, name: a.name }))))
      .catch((err) => console.error("Failed to fetch service areas:", err));
  }, []);

  const handleCreate = async () => {
    try {
      await createQuest({
        title: form.title,
        description: form.description,
        type: form.type,
        target_value: Number(form.target_value),
        reward_amount: Number(form.reward_amount),
        reward_type: form.reward_type,
        // datetime-local value is local wall-clock; toISOString() converts it to UTC.
        start_date: new Date(form.start_date).toISOString(),
        end_date: new Date(form.end_date).toISOString(),
        max_participants: form.max_participants ? Number(form.max_participants) : null,
        min_driver_rating: form.min_driver_rating ? Number(form.min_driver_rating) : null,
        // ALL_AREAS → null so the quest is global; otherwise scope it to the area
        // (the driver-facing /quests endpoint hides quests whose area ≠ the
        // driver's service_area_id).
        service_area_id: form.service_area_id === ALL_AREAS ? null : form.service_area_id,
      });
      setShowCreate(false);
      resetForm();
      fetchQuests();
    } catch (err) {
      console.error("Failed to create quest:", err);
    }
  };

  const handleToggleActive = async (quest: Quest) => {
    try {
      await updateQuest(quest.id, { is_active: !quest.is_active });
      fetchQuests();
    } catch (err) {
      console.error("Failed to update quest:", err);
    }
  };

  const handleExpandQuest = async (questId: string) => {
    if (expandedQuest === questId) {
      setExpandedQuest(null);
      return;
    }
    setExpandedQuest(questId);
    setParticipantsLoading(true);
    try {
      const data = await getQuestParticipants(questId);
      setParticipants(data);
    } catch (err) {
      console.error("Failed to fetch participants:", err);
      setParticipants([]);
    } finally {
      setParticipantsLoading(false);
    }
  };

  const resetForm = () => {
    setForm({
      title: "", description: "", type: "ride_count",
      target_value: 20, reward_amount: 25, reward_type: "wallet_credit",
      start_date: toLocalInput(new Date()),
      end_date: toLocalInput(new Date(Date.now() + 7 * 24 * 60 * 60 * 1000)),
      max_participants: "", min_driver_rating: "", service_area_id: ALL_AREAS,
    });
  };

  const formatDate = (d: string) =>
    new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });

  const isExpired = (endDate: string) => new Date(endDate) < new Date();

  const areaName = (id: string | null) =>
    id ? (serviceAreas.find((a) => a.id === id)?.name ?? "Scoped area") : "All areas";

  // Stats
  const totalQuests = quests.length;
  const activeQuests = quests.filter(q => q.is_active && !isExpired(q.end_date)).length;
  const totalParticipants = quests.reduce((s, q) => s + (q.stats?.total_participants || 0), 0);
  const totalRewardsPaid = quests.reduce((s, q) => s + (q.stats?.claimed || 0) * q.reward_amount, 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Trophy className="h-6 w-6 text-amber-500" />
            Quests & Bonus Challenges
          </h1>
          <p className="text-muted-foreground mt-1">
            Create and manage driver incentive challenges
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={fetchQuests} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4 mr-1" />
            Create Quest
          </Button>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4">
            <div className="text-sm text-muted-foreground">Total Quests</div>
            <div className="text-2xl font-bold">{totalQuests}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-sm text-muted-foreground">Active Now</div>
            <div className="text-2xl font-bold text-success">{activeQuests}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-sm text-muted-foreground">Total Participants</div>
            <div className="text-2xl font-bold">{totalParticipants}</div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4">
            <div className="text-sm text-muted-foreground">Rewards Paid</div>
            <div className="text-2xl font-bold text-amber-600">${totalRewardsPaid.toFixed(0)}</div>
          </CardContent>
        </Card>
      </div>

      {/* Quests Table */}
      <Card>
        <CardHeader>
          <CardTitle>All Quests</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="text-center py-8 text-muted-foreground">Loading quests...</div>
          ) : quests.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No quests yet. Create your first quest to start incentivizing drivers!
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableHead column="title" sort={questSort} onSort={toggleQuestSort}>Quest</SortableHead>
                  <SortableHead column="type" sort={questSort} onSort={toggleQuestSort}>Type</SortableHead>
                  <SortableHead column="target_value" sort={questSort} onSort={toggleQuestSort} align="right">Target</SortableHead>
                  <SortableHead column="reward_amount" sort={questSort} onSort={toggleQuestSort} align="right">Reward</SortableHead>
                  <SortableHead column="start_date" sort={questSort} onSort={toggleQuestSort}>Duration</SortableHead>
                  <SortableHead column="stats.total_participants" sort={questSort} onSort={toggleQuestSort} align="right">Participants</SortableHead>
                  <SortableHead column="is_active" sort={questSort} onSort={toggleQuestSort}>Status</SortableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedQuests.map((quest) => (
                  <>
                    <TableRow key={quest.id} className="cursor-pointer hover:bg-muted/50" onClick={() => handleExpandQuest(quest.id)}>
                      <TableCell>
                        <div className="font-medium">{quest.title}</div>
                        <div className="text-xs text-muted-foreground truncate max-w-[200px]">
                          {quest.description}
                        </div>
                        <Badge variant="outline" className="text-[10px] mt-1 font-normal">
                          {areaName(quest.service_area_id)}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">
                          {QUEST_TYPES.find(t => t.value === quest.type)?.label || quest.type}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono">
                        {quest.target_value}
                      </TableCell>
                      <TableCell>
                        <span className="font-semibold text-amber-600">${quest.reward_amount}</span>
                        <br />
                        <span className="text-xs text-muted-foreground">{quest.reward_type}</span>
                      </TableCell>
                      <TableCell className="text-xs">
                        {formatDate(quest.start_date)}
                        <br />
                        {formatDate(quest.end_date)}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Users className="h-3 w-3" />
                          <span>{quest.stats?.total_participants || 0}</span>
                          {(quest.stats?.completed || 0) > 0 && (
                            <Badge variant="secondary" className="text-xs ml-1">
                              {quest.stats?.completed} done
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        {isExpired(quest.end_date) ? (
                          <Badge className="bg-muted text-muted-foreground">Expired</Badge>
                        ) : quest.is_active ? (
                          <Badge className="bg-success/15 text-success">Active</Badge>
                        ) : (
                          <Badge className="bg-muted text-muted-foreground">Paused</Badge>
                        )}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                          <Switch
                            checked={quest.is_active}
                            onCheckedChange={() => handleToggleActive(quest)}
                          />
                          {expandedQuest === quest.id ? (
                            <ChevronUp className="h-4 w-4 text-muted-foreground" />
                          ) : (
                            <ChevronDown className="h-4 w-4 text-muted-foreground" />
                          )}
                        </div>
                      </TableCell>
                    </TableRow>

                    {/* Expanded Participants */}
                    {expandedQuest === quest.id && (
                      <TableRow key={`${quest.id}-detail`}>
                        <TableCell colSpan={8} className="bg-muted/30">
                          {participantsLoading ? (
                            <div className="text-center py-4 text-muted-foreground">Loading participants...</div>
                          ) : participants.length === 0 ? (
                            <div className="text-center py-4 text-muted-foreground">No participants yet</div>
                          ) : (
                            <div className="space-y-2 py-2">
                              <h4 className="font-semibold text-sm flex items-center gap-1">
                                <BarChart3 className="h-4 w-4" /> Participants ({participants.length})
                              </h4>
                              <Table>
                                <TableHeader>
                                  <TableRow>
                                    <SortableHead column="driver_name" sort={participantSort} onSort={toggleParticipantSort}>Driver</SortableHead>
                                    <SortableHead column="progress_pct" sort={participantSort} onSort={toggleParticipantSort} align="right">Progress</SortableHead>
                                    <SortableHead column="status" sort={participantSort} onSort={toggleParticipantSort}>Status</SortableHead>
                                    <SortableHead column="started_at" sort={participantSort} onSort={toggleParticipantSort}>Started</SortableHead>
                                  </TableRow>
                                </TableHeader>
                                <TableBody>
                                  {sortedParticipants.map((p) => (
                                    <TableRow key={p.progress_id}>
                                      <TableCell className="font-medium">{p.driver_name}</TableCell>
                                      <TableCell>
                                        <div className="flex items-center gap-2">
                                          <div className="w-24 h-2 bg-muted rounded-full overflow-hidden">
                                            <div
                                              className="h-full bg-blue-500 rounded-full"
                                              style={{ width: `${Math.min(100, p.progress_pct)}%` }}
                                            />
                                          </div>
                                          <span className="text-xs font-mono">
                                            {p.current_value}/{p.target_value} ({p.progress_pct}%)
                                          </span>
                                        </div>
                                      </TableCell>
                                      <TableCell>
                                        <Badge className={STATUS_COLORS[p.status] || "bg-muted"}>
                                          {p.status}
                                        </Badge>
                                      </TableCell>
                                      <TableCell className="text-xs">
                                        {formatDate(p.started_at)}
                                      </TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    )}
                  </>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create Quest Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="sm:max-w-[500px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trophy className="h-5 w-5 text-amber-500" />
              Create New Quest
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <Label htmlFor="title">Title</Label>
              <Input id="title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g., Weekend Warrior" />
            </div>
            <div>
              <Label htmlFor="description">Description</Label>
              <Input id="description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Complete 20 rides this weekend to earn a bonus!" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label>Type</Label>
                <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CREATABLE_QUEST_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor="target">Target Value</Label>
                <Input id="target" type="number" value={form.target_value} onChange={(e) => setForm({ ...form, target_value: Number(e.target.value) })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="reward">Reward Amount ($)</Label>
                <Input id="reward" type="number" value={form.reward_amount} onChange={(e) => setForm({ ...form, reward_amount: Number(e.target.value) })} />
              </div>
              <div>
                <Label>Reward Type</Label>
                <Select value={form.reward_type} onValueChange={(v) => setForm({ ...form, reward_type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="wallet_credit">Wallet Credit</SelectItem>
                    <SelectItem value="cash">Cash</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="start">Start Date</Label>
                <Input id="start" type="datetime-local" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="end">End Date</Label>
                <Input id="end" type="datetime-local" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="maxp">Max Participants (optional)</Label>
                <Input id="maxp" type="number" value={form.max_participants} onChange={(e) => setForm({ ...form, max_participants: e.target.value })} placeholder="Unlimited" />
              </div>
              <div>
                <Label htmlFor="minr">Min Driver Rating (optional)</Label>
                <Input id="minr" type="number" step="0.1" value={form.min_driver_rating} onChange={(e) => setForm({ ...form, min_driver_rating: e.target.value })} placeholder="Any" />
              </div>
            </div>
            <div>
              <Label>Service Area</Label>
              <Select value={form.service_area_id} onValueChange={(v) => setForm({ ...form, service_area_id: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_AREAS}>All areas</SelectItem>
                  {serviceAreas.map((a) => (
                    <SelectItem key={a.id} value={a.id}>{a.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground mt-1">
                Scoped quests only show to drivers whose home service area matches.
              </p>
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => { setShowCreate(false); resetForm(); }}>Cancel</Button>
            <Button onClick={handleCreate} disabled={!form.title || !form.description}>
              <Plus className="h-4 w-4 mr-1" />
              Create Quest
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
