import RallyKit
import SwiftUI

/// The packing list: every item grouped by location in walking order, exportable.
struct GoListView: View {
    let client: APIClient?
    @State private var list: GoList?
    @State private var failed = false
    @State private var exported: ExportedFile?
    @State private var exporting = false

    var body: some View {
        List {
            if let list {
                if list.totalItems == 0 {
                    ContentUnavailableView("Nothing to pack", systemImage: "suitcase").listRowBackground(Color.clear)
                }
                ForEach(list.groups) { group in
                    Section(group.locationName) {
                        ForEach(group.items) { item in
                            HStack(alignment: .firstTextBaseline) {
                                Image(systemName: "square").foregroundStyle(RallyDesign.color("inkSubtle"))
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.name)
                                    if let n = item.notes, !n.isEmpty {
                                        Text(n).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                                    }
                                }
                                Spacer()
                                if let q = item.quantity, !q.isEmpty { Text(q).foregroundStyle(RallyDesign.color("inkMuted")) }
                            }
                            .frame(minHeight: RallyDesign.targetMin)
                        }
                    }
                }
                Section { Text("\(list.totalItems) item\(list.totalItems == 1 ? "" : "s") · \(list.generatedOn)")
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted")) }
            } else if failed {
                ContentUnavailableView("Couldn't load the go list", systemImage: "wifi.exclamationmark")
            } else { ProgressView().frame(maxWidth: .infinity) }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Go List")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    ForEach([("PDF", "pdf"), ("Markdown", "md"), ("CSV", "csv")], id: \.1) { label, format in
                        Button(label) { Task { await export(format) } }
                    }
                } label: { Image(systemName: "square.and.arrow.up") }
                    .disabled(list == nil || exporting).accessibilityLabel("Export go list")
            }
        }
        .task { await load() }
        .refreshable { await load() }
        .sheet(item: $exported) { ShareSheet(url: $0.url) }
    }

    private func load() async {
        do { list = try await client?.goList(); failed = false } catch { if list == nil { failed = true } }
    }

    private func export(_ format: String) async {
        exporting = true
        defer { exporting = false }
        guard let file = try? await client?.exportGoList(format: format) else { return }
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(file.filename)
        try? file.data.write(to: url)
        exported = ExportedFile(url: url)
    }
}

struct ExportedFile: Identifiable { let url: URL; var id: URL { url } }

/// The system share sheet for a file we just wrote.
struct ShareSheet: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIActivityViewController { UIActivityViewController(activityItems: [url], applicationActivities: nil) }
    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
