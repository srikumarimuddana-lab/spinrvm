// Demand Forecast now lives as a tab on the Analytics page, which renders the
// same DemandForecastPanel component. This route is kept purely so existing
// bookmarks and links land in the right place instead of 404ing.
import { redirect } from "next/navigation";

export default function ForecastPage() {
    redirect("/dashboard/analytics?tab=forecast");
}
