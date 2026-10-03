// Copy into Devsy pkg/devcontainer at revision
// 0296937cf802061c0d25e270f66d7ba74872bf68 and run go test -run TestFridaySourceParity.
// Exercises the real workload-host resolver, not a controller mock.
package devcontainer

import (
    "os"
    "path/filepath"
    "testing"

    "github.com/devsy-org/devsy/pkg/provider"
)

func TestFridaySourceParity(t *testing.T) {
    const image = "ghcr.io/joshyorko/room-of-requirement:secure@sha256:de48bf9e2c93f8e358fcb5ac13e3159473245f5f271456c8f7d4dcc2ab881338"
    sources := map[string]provider.WorkspaceSource{
        "git": {GitRepository: "https://github.com/example/unrelated.git"},
        "local": {LocalFolder: "/controller/project with spaces"},
        "image": {Image: image},
    }
    for name, source := range sources {
        t.Run(name, func(t *testing.T) {
            folder := t.TempDir()
            original := []byte(`{"image":"unapproved:latest","features":{"unwanted":{}}}`)
            root := filepath.Join(folder, ".devcontainer.json")
            if err := os.WriteFile(root, original, 0600); err != nil { t.Fatal(err) }
            r := newRunnerAt(folder)
            r.workspaceConfig.Workspace.Source = source
            // An absolute controller path is NOT transferred by Devsy.
            _, err := r.getRawConfig(provider.CLIOptions{DevContainerSource: filepath.Join(folder, "missing-controller", "friday-remote", "devcontainer.json")})
            if err == nil { t.Fatal("controller-only path must fail on workload host") }
            opts := provider.CLIOptions{DevContainerSource: "image:" + image, RemoteUser: "vscode"}
            conf, err := r.getRawConfig(opts)
            if err != nil { t.Fatal(err) }
            if conf.Image != image || conf.RemoteUser != "vscode" || len(conf.Features) != 0 {
                t.Fatalf("wrong image/user or project config leaked: %+v", conf)
            }
            // Persisted native selection remains source-independent on resume.
            r.workspaceConfig.Workspace.DevContainerSource = opts.DevContainerSource
            resumed, err := r.getRawConfig(provider.CLIOptions{RemoteUser: "vscode"})
            if err != nil || resumed.Image != image { t.Fatalf("resume: %+v %v", resumed, err) }
            got, err := os.ReadFile(root)
            if err != nil || string(got) != string(original) { t.Fatal("project config changed") }
        })
    }
}

