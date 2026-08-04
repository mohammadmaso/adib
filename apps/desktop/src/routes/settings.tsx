import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, EyeOff, KeyRound, Loader } from "lucide-react";
import { toast } from "sonner";
import { api, type components } from "@/lib/api-client";
import { clearApiKey, getApiKey, setApiKey } from "@/lib/keychain";
import { projectIdFromPath } from "@/lib/project";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type ProviderSettings = components["schemas"]["ProviderSettings"];

const DEFAULT_PROVIDER: ProviderSettings = {
  base_url: "https://api.openai.com/v1",
  model: "gpt-4o-mini",
  temperature: 0.3,
  concurrency: 4,
  max_retries: 5,
  request_timeout_s: 180,
  price_per_mtok_in: 0,
  price_per_mtok_out: 0,
};

export default function SettingsRoute() {
  return (
    <div className="mx-auto max-w-xl space-y-8 p-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted-foreground">Provider credentials and per-project defaults.</p>
      </div>

      <ApiKeyCard />
      <ProviderCard />
    </div>
  );
}

function ApiKeyCard() {
  const [key, setKey] = useState("");
  const [hasKey, setHasKey] = useState<boolean | null>(null);
  const [reveal, setReveal] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getApiKey()
      .then((k) => {
        setHasKey(!!k);
        if (k) setKey(k);
      })
      .catch(() => setHasKey(false));
  }, []);

  async function save() {
    setSaving(true);
    try {
      await setApiKey(key);
      setHasKey(true);
      toast.success("API key saved to your OS keychain");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to save API key");
    } finally {
      setSaving(false);
    }
  }

  async function clear() {
    setSaving(true);
    try {
      await clearApiKey();
      setKey("");
      setHasKey(false);
      toast.success("API key cleared");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to clear API key");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyRound className="size-4" aria-hidden />
          API Key
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          Stored in your OS keychain, never in a project file or log. Used for every provider call
          across all projects.
        </p>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Input
              type={reveal ? "text" : "password"}
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder={hasKey === false ? "Not set" : "sk-…"}
              className="pr-9"
            />
            <button
              type="button"
              className="absolute top-1/2 right-2 -translate-y-1/2 text-muted-foreground"
              onClick={() => setReveal((r) => !r)}
              aria-label={reveal ? "Hide key" : "Show key"}
            >
              {reveal ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!key || saving} onClick={save}>
            {saving && <Loader className="size-3.5 animate-spin" aria-hidden />}
            Save
          </Button>
          <Button size="sm" variant="outline" disabled={hasKey === false || saving} onClick={clear}>
            Clear
          </Button>
          {hasKey !== null && (
            <span className="text-xs text-muted-foreground">
              {hasKey ? "A key is set" : "No key set"}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function ProviderCard() {
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState<string | null>(null);
  const [provider, setProvider] = useState<ProviderSettings | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: async () => {
      const { data, error } = await api.GET("/projects");
      if (error) throw error;
      return data;
    },
  });

  useEffect(() => {
    if (!projectId && projectsQuery.data && projectsQuery.data.length > 0) {
      setProjectId(projectIdFromPath(projectsQuery.data[0].path));
    }
  }, [projectId, projectsQuery.data]);

  const providerQuery = useQuery({
    queryKey: ["provider", projectId],
    enabled: !!projectId,
    queryFn: async () => {
      const { data, error } = await api.GET("/projects/{project_id}/provider", {
        params: { path: { project_id: projectId! } },
      });
      if (error) throw error;
      return data;
    },
  });

  useEffect(() => {
    if (providerQuery.data) setProvider(providerQuery.data);
  }, [providerQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!projectId || !provider) throw new Error("nothing to save");
      const { data, error } = await api.PUT("/projects/{project_id}/provider", {
        params: { path: { project_id: projectId } },
        body: provider,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: (data) => {
      setProvider(data);
      queryClient.invalidateQueries({ queryKey: ["provider", projectId] });
      toast.success("Provider settings saved");
    },
    onError: (error: unknown) => {
      toast.error(error instanceof Error ? error.message : "Failed to save provider settings");
    },
  });

  function set<K extends keyof ProviderSettings>(key: K, value: ProviderSettings[K]) {
    setProvider((p) => ({ ...(p ?? DEFAULT_PROVIDER), [key]: value }));
  }

  const projects = projectsQuery.data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Provider</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {projects.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No projects yet — provider settings are configured per project once one exists.
          </p>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label>Project</Label>
              <Select value={projectId ?? undefined} onValueChange={setProjectId}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.path} value={projectIdFromPath(p.path)}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {providerQuery.isLoading && (
              <div className="grid h-24 place-items-center">
                <Loader className="size-5 animate-spin text-muted-foreground" aria-hidden />
              </div>
            )}

            {provider && (
              <>
                <div className="grid grid-cols-2 gap-4">
                  <div className="col-span-2 space-y-1.5">
                    <Label>Base URL</Label>
                    <Input value={provider.base_url} onChange={(e) => set("base_url", e.target.value)} />
                  </div>
                  <div className="col-span-2 space-y-1.5">
                    <Label>Model</Label>
                    <Input value={provider.model} onChange={(e) => set("model", e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Temperature</Label>
                    <Input
                      type="number"
                      step="0.1"
                      min={0}
                      max={2}
                      value={provider.temperature}
                      onChange={(e) => set("temperature", Number(e.target.value))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Concurrency</Label>
                    <Input
                      type="number"
                      min={1}
                      value={provider.concurrency}
                      onChange={(e) => set("concurrency", Number(e.target.value))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Max retries</Label>
                    <Input
                      type="number"
                      min={0}
                      value={provider.max_retries}
                      onChange={(e) => set("max_retries", Number(e.target.value))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Request timeout (s)</Label>
                    <Input
                      type="number"
                      min={1}
                      value={provider.request_timeout_s}
                      onChange={(e) => set("request_timeout_s", Number(e.target.value))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Spend cap (USD)</Label>
                    <Input
                      type="number"
                      min={0}
                      step="0.01"
                      value={provider.max_spend_usd ?? ""}
                      placeholder="No cap"
                      onChange={(e) =>
                        set("max_spend_usd", e.target.value === "" ? null : Number(e.target.value))
                      }
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Price / Mtok in (USD)</Label>
                    <Input
                      type="number"
                      min={0}
                      step="0.01"
                      value={provider.price_per_mtok_in}
                      onChange={(e) => set("price_per_mtok_in", Number(e.target.value))}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label>Price / Mtok out (USD)</Label>
                    <Input
                      type="number"
                      min={0}
                      step="0.01"
                      value={provider.price_per_mtok_out}
                      onChange={(e) => set("price_per_mtok_out", Number(e.target.value))}
                    />
                  </div>
                </div>
                <Button disabled={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                  {saveMutation.isPending && <Loader className="size-4 animate-spin" aria-hidden />}
                  Save provider settings
                </Button>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
