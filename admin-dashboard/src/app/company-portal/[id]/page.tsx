"use client";

import { useEffect } from "react";
import { useParams, useRouter } from "next/navigation";

export default function CompanyPortalIndex() {
    const params = useParams();
    const router = useRouter();
    useEffect(() => {
        const id = typeof params?.id === "string" ? params.id : "";
        if (id) router.replace(`/company-portal/${id}/overview`);
    }, [params, router]);
    return null;
}
