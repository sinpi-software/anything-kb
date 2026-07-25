export interface KnowledgeBaseMembership {
  knowledge_base_id: string;
  knowledge_base_name: string;
  role: string;
}

export interface TypeDef {
  name: string;
  description: string;
  pinned: boolean;
  banned: boolean;
}

export interface KbConfig {
  interests: string;
  discover_types: boolean;
  entity_types: TypeDef[];
  relationship_types: TypeDef[];
}

export interface Me {
  id: string;
  email: string;
  name: string | null;
  email_verified: boolean;
  is_admin: boolean;
  knowledge_bases: KnowledgeBaseMembership[];
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
