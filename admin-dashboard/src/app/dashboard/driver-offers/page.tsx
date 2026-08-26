// Driver Offers now lives as a tab on the Analytics page, which renders the
// same DriverOffersPanel component. This route is kept purely so existing
// bookmarks and links land in the right place instead of 404ing.
//
// Server component on purpose — redirect() here is a real HTTP redirect, so
// the client never downloads a page it is only going to navigate away from.
import { redirect } from "next/navigation";

export default function DriverOffersPage() {
    redirect("/dashboard/analytics?tab=offers");
}
