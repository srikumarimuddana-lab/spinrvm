"use client";

import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useRequireModule } from "@/hooks/useRequireModule";
import { EntitySearchTable } from "@/components/data-transfer/EntitySearchTable";
import { useEntitySelection } from "@/components/data-transfer/useEntitySelection";
import { ExportTab } from "./ExportTab";
import { ImportTab } from "./ImportTab";
import { SgiFormsTab } from "./SgiFormsTab";
import { JobsTab } from "./JobsTab";

/**
 * Data Transfer module — consolidates the former Bulk Import (drivers) and
 * the rider-import section of Bulk Operations into one place, plus the new
 * export/SGI-forms capability. Search & Select feeds the Export and SGI
 * Forms tabs via a shared selection (useEntitySelection) — pick records
 * once, act on them from either tab.
 *
 * Export/Import/SGI Forms tab bodies land in follow-up subtasks; this shell
 * wires the Search tab (fully functional) and reserves the others so the
 * module's shape is visible end-to-end as each piece lands.
 */
export default function DataTransferPage() {
    const { allowed } = useRequireModule("bulk_operations");
    const selection = useEntitySelection();

    if (!allowed) return null;

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-semibold">Data Transfer</h1>
                <p className="text-muted-foreground">
                    Search, export, and import users and drivers — with documents, ride history, and the
                    insurance-period audit trail — between Spinr's own environments.
                </p>
            </div>

            <Tabs defaultValue="search">
                <TabsList>
                    <TabsTrigger value="search">Search &amp; Select</TabsTrigger>
                    <TabsTrigger value="export">Export</TabsTrigger>
                    <TabsTrigger value="import">Import</TabsTrigger>
                    <TabsTrigger value="sgi-forms">SGI Compliance Forms</TabsTrigger>
                    <TabsTrigger value="jobs">Jobs &amp; History</TabsTrigger>
                </TabsList>

                <TabsContent value="search">
                    <Card>
                        <CardHeader>
                            <CardTitle>Search &amp; Select</CardTitle>
                            <CardDescription>
                                Fuzzy search by name, email, or phone, with an optional date range. Select
                                individual records or every record matching your filter, then switch to Export or
                                SGI Compliance Forms to act on the selection.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <EntitySearchTable selection={selection} />
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="export">
                    <Card>
                        <CardHeader>
                            <CardTitle>Export</CardTitle>
                            <CardDescription>
                                Export the records selected in Search &amp; Select as a full ZIP bundle or a
                                profile-summary spreadsheet.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <ExportTab selection={selection} />
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="import">
                    <Card>
                        <CardHeader>
                            <CardTitle>Import</CardTitle>
                            <CardDescription>
                                Upload a bundle ZIP exported from another Spinr environment. Validate first — nothing
                                is written until you commit.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <ImportTab />
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="sgi-forms">
                    <Card>
                        <CardHeader>
                            <CardTitle>SGI Compliance Forms</CardTitle>
                            <CardDescription>
                                Fill the real SGI D00032/D00033 forms for the drivers selected in Search &amp; Select.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <SgiFormsTab selection={selection} />
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="jobs">
                    <Card>
                        <CardHeader>
                            <CardTitle>Jobs &amp; History</CardTitle>
                            <CardDescription>
                                Recent export batches, with re-downloadable links until they expire.
                            </CardDescription>
                        </CardHeader>
                        <CardContent>
                            <JobsTab />
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
