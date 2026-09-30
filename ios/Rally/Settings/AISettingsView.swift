import RallyKit
import SwiftUI

/// The AI's voice and what it knows about the family, plus which model speaks. Every save is a
/// snapshot you can go back to: a change that turns out worse is one tap from undone.
struct AISettingsView: View {
    @Environment(AppModel.self) private var app
    @State private var model: AIModel
    @State private var drafts: [String: String] = [:]
    @State private var notices: [String: String] = [:]
    @State private var historyField: HistoryField?

    init(client: APIClient) { _model = State(initialValue: AIModel(client: client)) }

    var body: some View {
        Form {
            do {
                ForEach(AIModel.fields, id: \.0) { field, title in
                    Section {
                        TextEditor(text: Binding(get: { drafts[field] ?? model.settings[field]?.value ?? "" }, set: { drafts[field] = $0 }))
                            .frame(minHeight: 140).accessibilityIdentifier("ai-\(field)")
                        Button("Save") { Task { await save(field, model) } }.disabled(drafts[field] == nil || drafts[field] == model.settings[field]?.value)
                        Button("Version history") { historyField = HistoryField(id: field, title: title) }
                        if let n = notices[field] { Text(n).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
                    } header: { Text(title) }
                }
                LLMSection(model: model)
            }
        }
        .navigationTitle("AI")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.load() }
        .sheet(item: $historyField) { field in AIHistoryView(model: model, field: field) { drafts[field.id] = nil } }
        .alert("Couldn't do that", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK") {}
        } message: { Text(model.errorMessage ?? "") }
    }

    private func save(_ field: String, _ model: AIModel) async {
        guard let value = drafts[field] else { return }
        if await model.save(field: field, value: value) { drafts[field] = nil; notices[field] = "Saved as a new version." }
    }
}

struct HistoryField: Identifiable { let id: String; let title: String }

struct AIHistoryView: View {
    @Environment(\.dismiss) private var dismiss
    let model: AIModel
    let field: HistoryField
    let changed: () -> Void
    @State private var history: AIHistory?
    @State private var expanded: Int?

    var body: some View {
        NavigationStack {
            List {
                ForEach(history?.history ?? []) { entry in
                    VStack(alignment: .leading, spacing: RallyDesign.space[2]) {
                        HStack {
                            Text(entry.createdAt.formatted(date: .abbreviated, time: .shortened)).font(.subheadline)
                            if entry.id == history?.currentHistoryID { Text("Current").font(.caption.bold()).padding(.horizontal, 6).background(RallyDesign.color("ink"), in: Capsule()).foregroundStyle(RallyDesign.color("surface")) }
                            Spacer()
                            Button(expanded == entry.id ? "Hide" : "Show") { expanded = expanded == entry.id ? nil : entry.id }.font(.footnote)
                        }
                        Text(expanded == entry.id ? entry.value : String(entry.value.prefix(120)) + (entry.value.count > 120 ? "…" : ""))
                            .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                        if entry.id != history?.currentHistoryID {
                            Button("Change version") { Task { if await model.rollback(field: field.id, to: entry.id) { changed(); dismiss() } } }
                        }
                    }
                }
            }
            .navigationTitle(field.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { history = await model.history(field: field.id) }
        }
    }
}

/// Provider, model and token budget as one coupled snapshot: a provider with the other's model is
/// not a configuration anybody wants to roll back to.
struct LLMSection: View {
    @Environment(AppModel.self) private var app
    let model: AIModel
    @State private var values: [String: String] = [:]
    @State private var provider = "anthropic"
    @State private var mode = "custom"
    @State private var result: String?
    @State private var working = false
    @State private var showingHistory = false
    @State private var loaded = false

    var body: some View {
        Section {
            Picker("Provider", selection: $provider) { Text("Anthropic").tag("anthropic"); Text("Local / OpenAI-compatible").tag("local") }
            if provider == "anthropic" {
                SecureField("API key", text: field("llm_anthropic_api_key")).textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Model", text: field("llm_anthropic_model")).textInputAutocapitalization(.never).autocorrectionDisabled()
                Picker("Token budget", selection: $mode) { Text("Model maximum").tag("model_max"); Text("Custom").tag("custom") }
                if mode == "custom" { TextField("Max tokens", text: field("llm_anthropic_max_tokens")).keyboardType(.numberPad) }
            } else {
                TextField("Base URL", text: field("llm_local_base_url")).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                SecureField("API key (if any)", text: field("llm_local_api_key")).textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Model", text: field("llm_local_model")).textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("Max tokens", text: field("llm_local_max_tokens")).keyboardType(.numberPad)
            }
            Button(working ? "Saving and checking…" : "Save and verify") { Task { await save() } }.disabled(working).accessibilityIdentifier("llm-save")
            if let result { Text(result).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
            Button("Version history") { showingHistory = true }
        } header: { Text("LLM") } footer: {
            Text(provider == "anthropic" && mode == "model_max"
                 ? "The model's real output limit is looked up when you save, and kept — the 4 AM job never makes that call itself."
                 : "Each provider keeps its own budget, so switching never carries one's onto the other.")
        }
        .task { await load() }
        .sheet(isPresented: $showingHistory) { LLMHistoryView(model: model) { Task { await load() } } }
    }

    private func field(_ key: String) -> Binding<String> { Binding(get: { values[key] ?? "" }, set: { values[key] = $0 }) }

    private func load() async {
        values = (try? await app.client?.settings()) ?? values
        await model.load()
        provider = model.llm?.provider ?? values["llm_provider"] ?? "anthropic"
        mode = model.llm?.maxTokensMode ?? values["llm_anthropic_max_tokens_mode"] ?? "custom"
        loaded = true
    }

    private func save() async {
        working = true
        defer { working = false }
        let modelKey = provider == "anthropic" ? "llm_anthropic_model" : "llm_local_model"
        let tokenKey = provider == "anthropic" ? "llm_anthropic_max_tokens" : "llm_local_max_tokens"
        var keys = values.filter { $0.key.hasPrefix("llm_") }
        keys["llm_provider"] = provider
        result = await model.saveLLM(keys: keys, provider: provider, model: values[modelKey] ?? "",
                                      maxTokens: Int(values[tokenKey] ?? "") ?? 0, mode: provider == "anthropic" ? mode : "custom")
        await load()
    }
}

struct LLMHistoryView: View {
    @Environment(\.dismiss) private var dismiss
    let model: AIModel
    let changed: () -> Void
    @State private var history: LLMHistory?

    var body: some View {
        NavigationStack {
            List {
                ForEach(history?.history ?? []) { entry in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(entry.createdAt.formatted(date: .abbreviated, time: .shortened)).font(.subheadline)
                            if entry.id == history?.currentHistoryID { Text("Current").font(.caption.bold()) }
                        }
                        Text("\(entry.provider) · \(entry.model) · \(entry.maxTokens) tokens\(entry.maxTokensMode == "model_max" ? " (model maximum)" : "")")
                            .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                        if entry.id != history?.currentHistoryID {
                            Button("Change version") { Task { if await model.rollbackLLM(to: entry.id) { changed(); dismiss() } } }
                        }
                    }
                }
            }
            .navigationTitle("LLM versions")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { history = await model.llmHistory() }
        }
    }
}
