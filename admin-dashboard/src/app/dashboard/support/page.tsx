"use client";

import { useEffect, useState } from "react";
import {
    getTickets,
    replyToTicket,
    closeTicket,
    getFaqs,
    createFaq,
    updateFaq,
    deleteFaq,
} from "@/lib/api";
import { formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import {
    MessageSquare,
    CheckCircle,
    Plus,
    Trash2,
    Pencil,
    Send,
} from "lucide-react";

export default function SupportPage() {
    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Support</h1>
                <p className="text-muted-foreground mt-1">
                    Manage support tickets and FAQs.
                </p>
            </div>

            <Tabs defaultValue="tickets">
                <TabsList>
                    <TabsTrigger value="tickets">Tickets</TabsTrigger>
                    <TabsTrigger value="faqs">FAQs</TabsTrigger>
                </TabsList>

                <TabsContent value="tickets" className="mt-4">
                    <TicketsTab />
                </TabsContent>

                <TabsContent value="faqs" className="mt-4">
                    <FaqsTab />
                </TabsContent>
            </Tabs>
        </div>
    );
}

/* ── Tickets ─────────────────────────────── */
function TicketsTab() {
    const [tickets, setTickets] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [selected, setSelected] = useState<any>(null);
    const [reply, setReply] = useState("");
    const [replying, setReplying] = useState(false);

    const fetchTickets = () => {
        setLoading(true);
        getTickets()
            .then(setTickets)
            .catch(() => { })
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        fetchTickets();
    }, []);

    const handleReply = async (id: string) => {
        if (!reply.trim()) return;
        setReplying(true);
        try {
            await replyToTicket(id, reply.trim());
            setReply("");
            fetchTickets();
        } catch {
        } finally {
            setReplying(false);
        }
    };

    const handleClose = async (id: string) => {
        try {
            await closeTicket(id);
            setSelected(null);
            fetchTickets();
        } catch { }
    };

    return (
        <div className="grid gap-4 lg:grid-cols-[1fr_400px]">
            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Subject</TableHead>
                                    <TableHead>Category</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Date</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {tickets.length === 0 ? (
                                    <TableRow>
                                        <TableCell
                                            colSpan={4}
                                            className="text-center text-muted-foreground py-12"
                                        >
                                            No tickets yet.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    tickets.map((ticket) => (
                                        <TableRow
                                            key={ticket.id}
                                            className="cursor-pointer hover:bg-muted/50"
                                            onClick={() => setSelected(ticket)}
                                        >
                                            <TableCell className="font-medium max-w-[200px] truncate">
                                                {ticket.subject}
                                            </TableCell>
                                            <TableCell className="text-muted-foreground">
                                                {ticket.category || "General"}
                                            </TableCell>
                                            <TableCell>
                                                <Badge
                                                    variant="secondary"
                                                    className={statusColor(ticket.status)}
                                                >
                                                    {ticket.status?.replace(/_/g, " ")}
                                                </Badge>
                                            </TableCell>
                                            <TableCell className="text-xs text-muted-foreground">
                                                {formatDate(ticket.created_at)}
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Ticket Detail Panel */}
            <Card className="border-border/50 h-fit">
                {selected ? (
                    <>
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-base">{selected.subject}</CardTitle>
                                <Badge
                                    variant="secondary"
                                    className={statusColor(selected.status)}
                                >
                                    {selected.status}
                                </Badge>
                            </div>
                            <p className="text-xs text-muted-foreground mt-1">
                                {formatDate(selected.created_at)} · {selected.category || "General"}
                            </p>
                        </CardHeader>
                        <Separator />
                        <CardContent className="pt-4 space-y-4">
                            <div className="rounded-lg bg-muted/50 p-3 text-sm">
                                {selected.message || selected.description || "No message."}
                            </div>

                            {/* Replies */}
                            {selected.replies?.map((r: any, i: number) => (
                                <div
                                    key={i}
                                    className="rounded-lg bg-primary/5 border border-primary/10 p-3 text-sm"
                                >
                                    <p className="text-xs text-muted-foreground mb-1">
                                        Admin Reply · {formatDate(r.created_at)}
                                    </p>
                                    {r.message}
                                </div>
                            ))}

                            {selected.status !== "closed" && (
                                <>
                                    <div className="flex gap-2">
                                        <Textarea
                                            placeholder="Type a reply..."
                                            value={reply}
                                            onChange={(e) => setReply(e.target.value)}
                                            rows={3}
                                        />
                                    </div>
                                    <div className="flex gap-2">
                                        <Button
                                            className="flex-1"
                                            onClick={() => handleReply(selected.id)}
                                            disabled={replying || !reply.trim()}
                                        >
                                            <Send className="mr-2 h-4 w-4" />
                                            {replying ? "Sending..." : "Reply"}
                                        </Button>
                                        <Button
                                            variant="outline"
                                            onClick={() => handleClose(selected.id)}
                                        >
                                            <CheckCircle className="mr-2 h-4 w-4" />
                                            Close
                                        </Button>
                                    </div>
                                </>
                            )}
                        </CardContent>
                    </>
                ) : (
                    <CardContent className="py-12 text-center text-muted-foreground">
                        <MessageSquare className="mx-auto mb-3 h-8 w-8 opacity-40" />
                        Select a ticket to view details.
                    </CardContent>
                )}
            </Card>
        </div>
    );
}

/* ── FAQs ────────────────────────────────── */
function FaqsTab() {
    const [faqs, setFaqs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [editing, setEditing] = useState<any>(null);
    const [form, setForm] = useState({ question: "", answer: "", category: "" });

    const fetchFaqs = () => {
        setLoading(true);
        getFaqs()
            .then(setFaqs)
            .catch(() => { })
            .finally(() => setLoading(false));
    };

    useEffect(() => {
        fetchFaqs();
    }, []);

    const handleSave = async () => {
        try {
            if (editing) {
                await updateFaq(editing.id, form);
            } else {
                await createFaq(form);
            }
            setDialogOpen(false);
            setEditing(null);
            setForm({ question: "", answer: "", category: "" });
            fetchFaqs();
        } catch { }
    };

    const handleDelete = async (id: string) => {
        if (!confirm("Delete this FAQ?")) return;
        try {
            await deleteFaq(id);
            fetchFaqs();
        } catch { }
    };

    return (
        <div className="space-y-4">
            <div className="flex justify-end">
                <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                    <DialogTrigger asChild>
                        <Button
                            onClick={() => {
                                setEditing(null);
                                setForm({ question: "", answer: "", category: "" });
                            }}
                        >
                            <Plus className="mr-2 h-4 w-4" /> Add FAQ
                        </Button>
                    </DialogTrigger>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>{editing ? "Edit" : "Create"} FAQ</DialogTitle>
                        </DialogHeader>
                        <div className="space-y-4 pt-4">
                            <div className="space-y-2">
                                <Label>Category</Label>
                                <Input
                                    value={form.category}
                                    onChange={(e) =>
                                        setForm({ ...form, category: e.target.value })
                                    }
                                    placeholder="Rides, Payments, Safety..."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Question</Label>
                                <Input
                                    value={form.question}
                                    onChange={(e) =>
                                        setForm({ ...form, question: e.target.value })
                                    }
                                    placeholder="How do I cancel a ride?"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Answer</Label>
                                <Textarea
                                    value={form.answer}
                                    onChange={(e) =>
                                        setForm({ ...form, answer: e.target.value })
                                    }
                                    placeholder="To cancel a ride..."
                                    rows={5}
                                />
                            </div>
                            <Button className="w-full" onClick={handleSave}>
                                {editing ? "Update" : "Create"}
                            </Button>
                        </div>
                    </DialogContent>
                </Dialog>
            </div>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Question</TableHead>
                                    <TableHead>Category</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {faqs.length === 0 ? (
                                    <TableRow>
                                        <TableCell
                                            colSpan={3}
                                            className="text-center text-muted-foreground py-12"
                                        >
                                            No FAQs yet.
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    faqs.map((faq) => (
                                        <TableRow key={faq.id}>
                                            <TableCell className="font-medium max-w-[300px] truncate">
                                                {faq.question}
                                            </TableCell>
                                            <TableCell className="text-muted-foreground">
                                                {faq.category || "General"}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <div className="flex justify-end gap-2">
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => {
                                                            setEditing(faq);
                                                            setForm({
                                                                question: faq.question || "",
                                                                answer: faq.answer || "",
                                                                category: faq.category || "",
                                                            });
                                                            setDialogOpen(true);
                                                        }}
                                                    >
                                                        <Pencil className="h-4 w-4" />
                                                    </Button>
                                                    <Button
                                                        variant="ghost"
                                                        size="icon"
                                                        className="text-destructive"
                                                        onClick={() => handleDelete(faq.id)}
                                                    >
                                                        <Trash2 className="h-4 w-4" />
                                                    </Button>
                                                </div>
                                            </TableCell>
                                        </TableRow>
                                    ))
                                )}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
