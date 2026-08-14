import { redirect } from "next/navigation";

export default function NotificationsRedirect(): never {
    redirect("/dashboard/cloud-messaging");
}
