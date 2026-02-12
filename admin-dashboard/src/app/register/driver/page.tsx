"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { User, Car, FileText, Check, Upload, AlertCircle, ChevronRight, ChevronLeft, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Alert, AlertDescription, AlertTitle } from "../../../components/ui/alert";

export default function DriverRegistrationPage() {
    const router = useRouter();
    const [step, setStep] = useState(0); // 0: Phone, 1: Verify, 2: Personal, 3: Vehicle, 4: Docs, 5: Success
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [vehicleTypes, setVehicleTypes] = useState<any[]>([]);

    // Form Data
    const [formData, setFormData] = useState({
        // Auth
        phone: "",
        otp: "",
        token: "",

        // Personal
        first_name: "",
        last_name: "",
        email: "",
        city: "",

        // Vehicle
        vehicle_make: "",
        vehicle_model: "",
        vehicle_color: "",
        vehicle_year: "",
        license_plate: "",
        vehicle_vin: "",
        vehicle_type_id: "",

        // Documents (URLs)
        license_number: "",
        license_expiry_date: "",
        work_eligibility_expiry_date: "",
        vehicle_inspection_expiry_date: "",
        insurance_expiry_date: "",
        background_check_expiry_date: "",

        documents: {} as Record<string, string>
    });

    const [files, setFiles] = useState<Record<string, File | null>>({
        license_front: null,
        license_back: null,
        insurance: null,
        registration: null,
        inspection: null,
        background_check: null
    });

    useEffect(() => {
        // Fetch vehicle types for public
        fetch("/api/admin/vehicle-types")
            .then(res => res.json())
            .then(data => setVehicleTypes(data))
            .catch(console.error);
    }, []);

    const handleChange = (field: string, value: string) => {
        setFormData(prev => ({ ...prev, [field]: value }));
        setError("");
    };

    const handleFileChange = (field: string, file: File | null) => {
        setFiles(prev => ({ ...prev, [field]: file }));
    };

    // Step 0: Send OTP
    const sendOtp = async () => {
        if (!formData.phone) return setError("Phone number is required");
        setLoading(true);
        try {
            const res = await fetch("/api/auth/send-otp", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ phone: formData.phone })
            });
            if (!res.ok) throw new Error("Failed to send OTP");
            setStep(1);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    // Step 1: Verify OTP
    const verifyOtp = async () => {
        if (!formData.otp) return setError("Enter OTP");
        setLoading(true);
        try {
            const res = await fetch("/api/auth/verify-otp", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ phone: formData.phone, code: formData.otp })
            });
            if (!res.ok) throw new Error("Invalid OTP");

            const data = await res.json();
            setFormData(prev => ({ ...prev, token: data.token }));

            // If new user or existing rider, proceed to registration
            // If already driver? The backend should handle or we check profile
            if (data.user?.role === 'driver') {
                // Maybe show "Already registered"?
                // For now, let's assume they want to "register" again or update? 
                // Backend blocks register if existing driver.
                // We'll proceed.
            }
            setStep(2);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    // Helper: Upload a single file
    const uploadFile = async (file: File): Promise<string> => {
        const data = new FormData();
        data.append("file", file);
        const res = await fetch("/api/upload", {
            method: "POST",
            body: data,
            // Note: Content-Type header is auto-set by browser with boundary for FormData
        });
        if (!res.ok) throw new Error("Upload failed");
        const json = await res.json();
        return json.url;
    };

    // Step 4: Submit Registration
    const submitRegistration = async () => {
        setLoading(true);
        setError("");

        try {
            // 1. Upload all files
            const docUrls: Record<string, string> = {};
            for (const [key, file] of Object.entries(files)) {
                if (file) {
                    docUrls[key] = await uploadFile(file);
                }
            }

            // 2. Prepare payload
            const payload = {
                first_name: formData.first_name,
                last_name: formData.last_name,
                email: formData.email,
                city: formData.city,

                vehicle_make: formData.vehicle_make,
                vehicle_model: formData.vehicle_model,
                vehicle_color: formData.vehicle_color,
                vehicle_year: parseInt(formData.vehicle_year),
                license_plate: formData.license_plate,
                vehicle_vin: formData.vehicle_vin,
                vehicle_type_id: formData.vehicle_type_id,

                license_number: formData.license_number,
                license_expiry_date: new Date(formData.license_expiry_date).toISOString(),
                work_eligibility_expiry_date: formData.work_eligibility_expiry_date ? new Date(formData.work_eligibility_expiry_date).toISOString() : null,
                vehicle_inspection_expiry_date: new Date(formData.vehicle_inspection_expiry_date).toISOString(),
                insurance_expiry_date: new Date(formData.insurance_expiry_date).toISOString(),
                background_check_expiry_date: new Date(formData.background_check_expiry_date).toISOString(),

                documents: docUrls
            };

            // 3. Register
            const res = await fetch("/api/drivers/register", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${formData.token}`
                },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || "Registration failed");
            }

            setStep(5); // Success

        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const validateVehicleYear = () => {
        const year = parseInt(formData.vehicle_year);
        const currentYear = new Date().getFullYear();
        if (currentYear - year > 9) {
            setError("Vehicle must be 9 years old or newer (Saskatchewan regulation).");
            return false;
        }
        return true;
    };

    const nextStep = () => {
        setError("");
        if (step === 3) { // Vehicle Info
            if (!validateVehicleYear()) return;
        }
        setStep(s => s + 1);
    };

    const renderStepContent = () => {
        switch (step) {
            case 0: return (
                <div className="space-y-4">
                    <div className="space-y-2">
                        <Label>Phone Number</Label>
                        <Input placeholder="+1306..." value={formData.phone} onChange={e => handleChange("phone", e.target.value)} />
                    </div>
                </div>
            );
            case 1: return (
                <div className="space-y-4">
                    <div className="space-y-2">
                        <Label>Enter OTP sent to {formData.phone}</Label>
                        <Input placeholder="1234" value={formData.otp} onChange={e => handleChange("otp", e.target.value)} />
                    </div>
                </div>
            );
            case 2: return (
                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                        <Label>First Name</Label>
                        <Input value={formData.first_name} onChange={e => handleChange("first_name", e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Last Name</Label>
                        <Input value={formData.last_name} onChange={e => handleChange("last_name", e.target.value)} />
                    </div>
                    <div className="space-y-2 sm:col-span-2">
                        <Label>Email</Label>
                        <Input type="email" value={formData.email} onChange={e => handleChange("email", e.target.value)} />
                    </div>
                    <div className="space-y-2 sm:col-span-2">
                        <Label>City</Label>
                        <Input value={formData.city} onChange={e => handleChange("city", e.target.value)} />
                    </div>
                </div>
            );
            case 3: return (
                <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                        <Label>Vehicle Type</Label>
                        <Select onValueChange={v => handleChange("vehicle_type_id", v)}>
                            <SelectTrigger><SelectValue placeholder="Select type" /></SelectTrigger>
                            <SelectContent>
                                {vehicleTypes.map(vt => (
                                    <SelectItem key={vt.id} value={vt.id}>{vt.name}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-2">
                        <Label>Year</Label>
                        <Input type="number" placeholder="2020" value={formData.vehicle_year} onChange={e => handleChange("vehicle_year", e.target.value)} />
                        <p className="text-xs text-muted-foreground">Must be {new Date().getFullYear() - 9} or newer.</p>
                    </div>
                    <div className="space-y-2">
                        <Label>Make</Label>
                        <Input placeholder="Toyota" value={formData.vehicle_make} onChange={e => handleChange("vehicle_make", e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Model</Label>
                        <Input placeholder="Camry" value={formData.vehicle_model} onChange={e => handleChange("vehicle_model", e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>Color</Label>
                        <Input placeholder="White" value={formData.vehicle_color} onChange={e => handleChange("vehicle_color", e.target.value)} />
                    </div>
                    <div className="space-y-2">
                        <Label>License Plate</Label>
                        <Input placeholder="ABC 123" value={formData.license_plate} onChange={e => handleChange("license_plate", e.target.value)} />
                    </div>
                    <div className="space-y-2 sm:col-span-2">
                        <Label>VIN (Vehicle Identification Number)</Label>
                        <Input value={formData.vehicle_vin} onChange={e => handleChange("vehicle_vin", e.target.value)} />
                    </div>
                </div>
            );
            case 4: return (
                <div className="space-y-6">
                    <div className="space-y-4 border rounded-lg p-4">
                        <h4 className="font-medium flex items-center gap-2"><FileText className="h-4 w-4" /> Driver's License</h4>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label>License Number</Label>
                                <Input value={formData.license_number} onChange={e => handleChange("license_number", e.target.value)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Expiry Date</Label>
                                <Input type="date" value={formData.license_expiry_date} onChange={e => handleChange("license_expiry_date", e.target.value)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Front Photo</Label>
                                <Input type="file" onChange={e => handleFileChange("license_front", e.target.files?.[0] || null)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Back Photo</Label>
                                <Input type="file" onChange={e => handleFileChange("license_back", e.target.files?.[0] || null)} />
                            </div>
                        </div>
                    </div>

                    <div className="space-y-4 border rounded-lg p-4">
                        <h4 className="font-medium flex items-center gap-2"><Car className="h-4 w-4" /> Vehicle Documents</h4>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label>Inspection Expiry</Label>
                                <Input type="date" value={formData.vehicle_inspection_expiry_date} onChange={e => handleChange("vehicle_inspection_expiry_date", e.target.value)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Insurance Expiry</Label>
                                <Input type="date" value={formData.insurance_expiry_date} onChange={e => handleChange("insurance_expiry_date", e.target.value)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Upload Inspection</Label>
                                <Input type="file" onChange={e => handleFileChange("inspection", e.target.files?.[0] || null)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Upload Insurance</Label>
                                <Input type="file" onChange={e => handleFileChange("insurance", e.target.files?.[0] || null)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Upload Registration</Label>
                                <Input type="file" onChange={e => handleFileChange("registration", e.target.files?.[0] || null)} />
                            </div>
                        </div>
                    </div>

                    <div className="space-y-4 border rounded-lg p-4">
                        <h4 className="font-medium flex items-center gap-2"><Check className="h-4 w-4" /> Background Check</h4>
                        <div className="grid gap-4 sm:grid-cols-2">
                            <div className="space-y-2">
                                <Label>Expiry Date</Label>
                                <Input type="date" value={formData.background_check_expiry_date} onChange={e => handleChange("background_check_expiry_date", e.target.value)} />
                            </div>
                            <div className="space-y-2">
                                <Label>Upload Document</Label>
                                <Input type="file" onChange={e => handleFileChange("background_check", e.target.files?.[0] || null)} />
                            </div>
                        </div>
                    </div>
                </div>
            );
            case 5: return (
                <div className="text-center py-8 space-y-4">
                    <div className="mx-auto bg-green-100 p-4 rounded-full w-20 h-20 flex items-center justify-center">
                        <Check className="h-10 w-10 text-green-600" />
                    </div>
                    <h2 className="text-2xl font-bold">Application Submitted!</h2>
                    <p className="text-gray-500 max-w-md mx-auto">
                        Your application is now under review. We will notify you once your documents have been verified.
                    </p>
                    <Button onClick={() => router.push("/login")}>Go to Login</Button>
                </div>
            );
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
            <Card className="w-full max-w-2xl">
                <CardHeader>
                    <CardTitle className="text-xl">Become a Driver</CardTitle>
                    <CardDescription>
                        {step === 0 && "Get started with your phone number"}
                        {step === 1 && "Verify your phone number"}
                        {step === 2 && "Personal Information"}
                        {step === 3 && "Vehicle Details"}
                        {step === 4 && "Document Upload"}
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {error && (
                        <Alert variant="destructive" className="mb-4">
                            <AlertCircle className="h-4 w-4" />
                            <AlertTitle>Error</AlertTitle>
                            <AlertDescription>{error}</AlertDescription>
                        </Alert>
                    )}

                    {renderStepContent()}
                </CardContent>
                <CardFooter className="flex justify-between">
                    {step > 0 && step < 5 && (
                        <Button variant="outline" onClick={() => setStep(s => s - 1)} disabled={loading}>
                            Back
                        </Button>
                    )}
                    <div className="ml-auto">
                        {step === 0 && (
                            <Button onClick={sendOtp} disabled={loading}>
                                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Send Code
                            </Button>
                        )}
                        {step === 1 && (
                            <Button onClick={verifyOtp} disabled={loading}>
                                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Verify
                            </Button>
                        )}
                        {step > 1 && step < 4 && (
                            <Button onClick={nextStep}>
                                Next <ChevronRight className="ml-2 h-4 w-4" />
                            </Button>
                        )}
                        {step === 4 && (
                            <Button onClick={submitRegistration} disabled={loading}>
                                {loading && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Submit Application
                            </Button>
                        )}
                    </div>
                </CardFooter>
            </Card>
        </div>
    );
}
