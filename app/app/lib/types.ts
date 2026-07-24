export interface OrgMembership {
  org_id: string;
  org_name: string;
  role: string;
}

export interface Me {
  id: string;
  email: string;
  name: string | null;
  email_verified: boolean;
  is_admin: boolean;
  orgs: OrgMembership[];
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export interface CreatedApiKey {
  id: string;
  name: string;
  prefix: string;
  key: string;
}
