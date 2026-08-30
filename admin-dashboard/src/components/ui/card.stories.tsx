import type { Meta, StoryObj } from "@storybook/nextjs-vite"

import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "./card"
import { Button } from "./button"

const meta = {
  title: "ui/Card",
  component: Card,
  parameters: { layout: "centered" },
  tags: ["autodocs"],
} satisfies Meta<typeof Card>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  render: () => (
    <Card className="w-80">
      <CardHeader>
        <CardTitle>Driver document review</CardTitle>
        <CardDescription>Pending verification</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-muted-foreground">
          Vulnerable Sector Check uploaded 2 days ago.
        </p>
      </CardContent>
      <CardFooter className="gap-2">
        <Button size="sm">Approve</Button>
        <Button size="sm" variant="outline">
          Reject
        </Button>
      </CardFooter>
    </Card>
  ),
}
