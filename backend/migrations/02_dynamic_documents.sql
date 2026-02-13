-- Create document_requirements table
CREATE TABLE IF NOT EXISTS public.document_requirements (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    description TEXT,
    is_mandatory BOOLEAN DEFAULT true,
    requires_back_side BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc', now())
);

-- Update driver_documents table
ALTER TABLE public.driver_documents 
ADD COLUMN IF NOT EXISTS requirement_id UUID REFERENCES public.document_requirements(id),
ADD COLUMN IF NOT EXISTS side TEXT CHECK (side IN ('front', 'back'));

-- RLS for document_requirements
ALTER TABLE public.document_requirements ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access for requirements"
ON public.document_requirements FOR SELECT
TO authenticated, anon
USING (true);

CREATE POLICY "Admin full access for requirements"
ON public.document_requirements FOR ALL
TO authenticated
USING (
  EXISTS (
    SELECT 1 FROM public.users 
    WHERE public.users.id = auth.uid() 
    AND public.users.role = 'admin'
  )
);

-- Seed default requirements
INSERT INTO public.document_requirements (name, description, is_mandatory, requires_back_side)
SELECT 'Driving License', 'Valid driving license', true, true
WHERE NOT EXISTS (SELECT 1 FROM public.document_requirements WHERE name = 'Driving License');

INSERT INTO public.document_requirements (name, description, is_mandatory, requires_back_side)
SELECT 'Vehicle Insurance', 'Valid vehicle insurance policy', true, false
WHERE NOT EXISTS (SELECT 1 FROM public.document_requirements WHERE name = 'Vehicle Insurance');

INSERT INTO public.document_requirements (name, description, is_mandatory, requires_back_side)
SELECT 'Vehicle Inspection', 'Vehicle inspection report', true, false
WHERE NOT EXISTS (SELECT 1 FROM public.document_requirements WHERE name = 'Vehicle Inspection');
