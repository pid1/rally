import RallyKit
import SwiftUI

/// Items bought before today. Their own page rather than a toggle on the list:
/// it is different data with a different lifetime.
struct PurchasedView: View {
    @State private var model: PurchasedModel
    @State private var stores: [ShoppingStore] = []
    private let client: APIClient?

    init(client: APIClient?) {
        self.client = client
        _model = State(initialValue: PurchasedModel(client: client ?? APIClient(baseURL: URL(string: "http://invalid")!)))
    }

    private var groups: [ShoppingGroup] { ShoppingLogic.groups(stores: stores, items: model.items, selected: []) }

    var body: some View {
        List {
            // Filters live in the list: pinned with safeAreaInset, the horizontal scroller slid
            // under the navigation bar on some screens and left a blank band.
            let chips = model.chips(stores: stores)
            if !chips.isEmpty {
                Section {
                    ChipBar(chips: chips, selected: model.selected, toggle: model.toggleFilter)
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }
            if model.hasLoaded && model.items.isEmpty {
                ContentUnavailableView(model.search.isEmpty && model.selected.isEmpty ? "Nothing purchased yet" : "No matches",
                                       systemImage: "bag")
                    .listRowBackground(Color.clear)
            }
            ForEach(groups) { group in
                Section(group.title) {
                    ForEach(group.items) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(item.name)
                            if let note = item.note, !note.isEmpty {
                                Text(note).font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                            }
                            if let when = item.completedAt {
                                Text("Bought \(when.formatted(date: .abbreviated, time: .omitted))")
                                    .font(.caption).foregroundStyle(RallyDesign.color("inkSubtle"))
                            }
                        }
                        .frame(minHeight: RallyDesign.targetMin, alignment: .leading)
                    }
                }
            }
            if model.hasMore {
                Button { Task { await model.loadMore() } } label: {
                    Text(model.isLoading ? "Loading…" : "Load more").frame(maxWidth: .infinity, minHeight: RallyDesign.targetMin)
                }
                .accessibilityIdentifier("purchased-load-more")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Purchased")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: Bindable(model).search, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search purchased items")
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if model.hasLoaded {
                Text("\(model.total) purchased item\(model.total == 1 ? "" : "s")")
                    .font(.footnote).foregroundStyle(RallyDesign.color("inkMuted"))
                    .padding(RallyDesign.space[2]).frame(maxWidth: .infinity).background(.bar)
            }
        }
        // Search is debounced by the cancelled sleep; filters reload at once.
        .task(id: "\(model.search)|\(model.selected.sorted())") {
            if model.hasLoaded { try? await Task.sleep(for: .milliseconds(250)) }
            guard !Task.isCancelled else { return }
            await model.reload()
        }
        .task { stores = (try? await client?.shoppingStores()) ?? [] }
        .alert("Couldn't load that", isPresented: Binding(
            get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })
        ) { Button("OK") {} } message: { Text(model.errorMessage ?? "") }
    }
}
