import Foundation
import Testing
@testable import RallyKit

private func item(_ id: Int, _ name: String, store: Int? = nil, done: Bool = false, order: Int = 0) -> ShoppingItem {
    ShoppingItem(id: id, name: name, storeID: store, completed: done, sortOrder: order)
}

struct ShoppingLogicTests {
    let stores = [ShoppingStore(id: 2, name: "Trader Joe's"), ShoppingStore(id: 1, name: "costco"), ShoppingStore(id: 3, name: "Aldi")]

    @Test func groupsNamedStoresAlphabeticallyAndAnywhereLast() {
        let items = [item(1, "milk"), item(2, "eggs", store: 2), item(3, "tp", store: 1), item(4, "rice", store: 3)]
        let groups = ShoppingLogic.groups(stores: stores, items: items, selected: [])
        #expect(groups.map(\.title) == ["Aldi", "costco", "Trader Joe's", "Anywhere"])
        #expect(groups.last?.storeID == nil)
    }

    @Test func keepsTheServersOrderWithinAGroup() {
        let items = [item(5, "b", store: 1), item(6, "a", store: 1), item(7, "done", store: 1, done: true)]
        let group = ShoppingLogic.groups(stores: stores, items: items, selected: []).first!
        #expect(group.items.map(\.id) == [5, 6, 7])
        #expect(group.openItems.map(\.id) == [5, 6])
    }

    @Test func aChipNeedsAnItemOnScreenOrASelection() {
        let items = [item(1, "milk"), item(2, "tp", store: 1)]
        #expect(ShoppingLogic.chips(stores: stores, items: items, selected: []).map(\.title) == ["costco", "Anywhere"])
        // Filtered to a store whose last item was just ticked off elsewhere: the chip stays.
        #expect(ShoppingLogic.chips(stores: stores, items: [item(1, "milk")], selected: ["3"]).map(\.title) == ["Aldi", "Anywhere"])
    }

    @Test func aSelectedStoreWithNothingInItStillGetsAGroup() {
        let groups = ShoppingLogic.groups(stores: stores, items: [item(1, "milk")], selected: ["3"])
        #expect(groups.map(\.title) == ["Aldi"])
        #expect(groups[0].items.isEmpty)
    }

    @Test func filtersToTheSelectedStores() {
        let items = [item(1, "milk"), item(2, "tp", store: 1), item(3, "rice", store: 3)]
        let groups = ShoppingLogic.groups(stores: stores, items: items, selected: ["1", ShoppingLogic.anywhere])
        #expect(groups.map(\.title) == ["costco", "Anywhere"])
    }

    @Test func reorderMovesWithinTheGroup() {
        #expect(ShoppingLogic.reordered([10, 11, 12], from: IndexSet(integer: 2), to: 0) == [12, 10, 11])
        #expect(ShoppingLogic.reordered([10, 11, 12], from: IndexSet(integer: 0), to: 3) == [11, 12, 10])
    }

    @Test func anItemForADeletedStoreFallsBackToAnywhereTitle() {
        let groups = ShoppingLogic.groups(stores: [], items: [item(1, "x", store: 99)], selected: [])
        #expect(groups.first?.title == "Anywhere")
    }
}

struct ShoppingEndpointTests {
    let base = URL(string: "http://rally.test")!
    let itemJSON = Data(##"{"id":1,"name":"Milk","note":null,"store_id":null,"completed":false,"completed_at":null,"sort_order":0,"created_at":"2026-09-30T10:00:00","updated_at":"2026-09-30T10:00:00"}"##.utf8)

    private func body(_ rec: Recorder, _ index: Int = 0) -> [String: Any] {
        try! JSONSerialization.jsonObject(with: rec.requests[index].httpBody!) as! [String: Any]
    }

    @Test func createOmitsEmptyNoteAndAnywhere() async throws {
        let rec = Recorder(); rec.body = itemJSON
        _ = try await APIClient(baseURL: base, transport: rec.transport()).createShoppingItem(name: "Milk", note: "", storeID: nil)
        #expect(body(rec).keys.sorted() == ["name"])
        #expect(rec.requests[0].httpMethod == "POST")
    }

    @Test func updateCanClearTheNoteAndMoveToAnywhere() async throws {
        let rec = Recorder(); rec.body = itemJSON
        _ = try await APIClient(baseURL: base, transport: rec.transport())
            .updateShoppingItem(id: 1, note: .null, storeID: .null)
        let sent = body(rec)
        #expect(sent["note"] is NSNull)
        #expect(sent["store_id"] is NSNull)
        #expect(sent["name"] == nil && sent["completed"] == nil)
    }

    @Test func reorderToAnywhereSendsNullStore() async throws {
        let rec = Recorder(); rec.body = Data("[]".utf8)
        try await APIClient(baseURL: base, transport: rec.transport()).reorderShoppingItems(storeID: nil, itemIDs: [3, 1])
        let sent = body(rec)
        #expect(sent["store_id"] is NSNull)
        #expect(sent["item_ids"] as? [Int] == [3, 1])
        #expect(rec.requests[0].url?.path == "/api/shopping/items/reorder")
    }

    @Test func purchasedSendsSearchAndRepeatedStoreFilters() async throws {
        let rec = Recorder(); rec.body = Data(#"{"items":[],"has_more":false,"total":0,"stores":["1"]}"#.utf8)
        let page = try await APIClient(baseURL: base, transport: rec.transport())
            .purchasedItems(search: " milk ", stores: ["2", "anywhere"], limit: 50, offset: 100)
        #expect(page.stores == ["1"])
        let url = rec.requests[0].url!.absoluteString
        #expect(url.contains("search=milk") && url.contains("store=2") && url.contains("store=anywhere") && url.contains("offset=100"))
    }
}

@MainActor
struct ShoppingModelTests {
    let base = URL(string: "http://rally.test")!

    /// A tiny in-memory server for the endpoints the model calls.
    final class Fake: @unchecked Sendable {
        var items: [[String: Any]] = [
            ["id": 1, "name": "Milk", "store_id": NSNull(), "completed": false, "sort_order": 0],
            ["id": 2, "name": "Eggs", "store_id": NSNull(), "completed": false, "sort_order": 1],
        ]
        var failWrites = false
        var calls: [String] = []

        func transport() -> Transport {
            { [self] request in
                let path = request.url!.path, method = request.httpMethod!
                calls.append("\(method) \(path)")
                func reply(_ status: Int, _ json: Any) -> (Data, HTTPURLResponse) {
                    let data = try! JSONSerialization.data(withJSONObject: json)
                    return (data, HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!)
                }
                if method != "GET", failWrites { return reply(500, ["detail": "Nope"]) }
                switch (method, path) {
                case ("GET", "/api/shopping/stores"): return reply(200, [])
                case ("GET", "/api/shopping/items"):
                    let full = items.map { $0.merging(["created_at": "2026-09-30T10:00:00", "updated_at": "2026-09-30T10:00:00", "note": NSNull(), "completed_at": NSNull()]) { a, _ in a } }
                    return reply(200, full)
                case ("PUT", let p) where p.hasPrefix("/api/shopping/items/"):
                    let id = Int(p.split(separator: "/").last!)!
                    let patch = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                    let index = items.firstIndex { $0["id"] as? Int == id }!
                    for (k, v) in patch { items[index][k] = v }
                    return reply(200, items[index].merging(["created_at": "2026-09-30T10:00:00", "updated_at": "2026-09-30T10:00:00"]) { a, _ in a })
                case ("POST", "/api/shopping/items/reorder"):
                    let patch = try! JSONSerialization.jsonObject(with: request.httpBody!) as! [String: Any]
                    for (pos, id) in (patch["item_ids"] as! [Int]).enumerated() {
                        items[items.firstIndex { $0["id"] as? Int == id }!]["sort_order"] = pos
                    }
                    return reply(200, [])
                case ("DELETE", _): return reply(204, [:])
                default: return reply(404, ["detail": "unhandled \(method) \(path)"])
                }
            }
        }
    }

    @Test func loadsAndGroups() async {
        let fake = Fake()
        let model = ShoppingModel(client: APIClient(baseURL: base, transport: fake.transport()))
        await model.load()
        #expect(model.items.map(\.name) == ["Milk", "Eggs"])
        #expect(model.groups.map(\.title) == ["Anywhere"])
    }

    @Test func ticksAnItemOffAndPersistsIt() async {
        let fake = Fake()
        let model = ShoppingModel(client: APIClient(baseURL: base, transport: fake.transport()))
        await model.load()
        await model.setCompleted(model.items[0], true)
        #expect(fake.items[0]["completed"] as? Bool == true)
    }

    @Test func aFailedWriteSurfacesTheReasonAndRestoresTheList() async {
        let fake = Fake()
        let model = ShoppingModel(client: APIClient(baseURL: base, transport: fake.transport()))
        await model.load()
        fake.failWrites = true
        await model.setCompleted(model.items[0], true)
        #expect(model.errorMessage == "Nope")
        #expect(model.items[0].completed == false) // reloaded from the server's truth
    }

    @Test func dragReordersLocallyAndSendsTheGroupOrder() async {
        let fake = Fake()
        let model = ShoppingModel(client: APIClient(baseURL: base, transport: fake.transport()))
        await model.load()
        await model.move(group: ShoppingLogic.anywhere, from: IndexSet(integer: 1), to: 0)
        #expect(model.items.map(\.name) == ["Eggs", "Milk"])
        #expect(fake.items.first { $0["id"] as? Int == 2 }?["sort_order"] as? Int == 0)
    }

    @Test func editingOnlyStoreSendsNothingElse() async {
        let fake = Fake()
        let model = ShoppingModel(client: APIClient(baseURL: base, transport: fake.transport()))
        await model.load()
        let milk = model.items[0]
        await model.save(milk, name: "Milk", note: "", storeID: 7)
        #expect(fake.items[0]["store_id"] as? Int == 7)
        #expect(fake.items[0]["name"] as? String == "Milk")
        #expect(fake.items[0]["note"] == nil) // unchanged note is not sent
    }
}

@MainActor
struct PurchasedModelTests {
    /// Serves `total` purchased rows, honoring limit and offset.
    final class Server: @unchecked Sendable {
        let total = 5
        var queries: [String] = []
        func transport() -> Transport {
            { [self] request in
                let q = request.url!.query ?? ""
                queries.append(q)
                let items = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!
                let limit = Int(items.first { $0.name == "limit" }!.value!)!
                let offset = Int(items.first { $0.name == "offset" }!.value!)!
                let rows = (offset..<min(offset + limit, total)).map { i -> [String: Any] in
                    ["id": i, "name": "Item \(i)", "store_id": NSNull(), "completed": true, "completed_at": "2026-09-01T10:00:00",
                     "sort_order": 0, "created_at": "2026-08-01T10:00:00", "updated_at": "2026-09-01T10:00:00", "note": NSNull()]
                }
                let body: [String: Any] = ["items": rows, "has_more": offset + limit < total, "total": total, "stores": ["anywhere", "4"]]
                return (try! JSONSerialization.data(withJSONObject: body),
                        HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
            }
        }
    }

    @Test func pagesAndResetsOnFilterChange() async {
        let server = Server()
        let model = PurchasedModel(client: APIClient(baseURL: URL(string: "http://x")!, transport: server.transport()), pageSize: 2)
        await model.reload()
        #expect(model.items.count == 2 && model.hasMore && model.total == 5)
        await model.loadMore()
        await model.loadMore()
        #expect(model.items.count == 5 && !model.hasMore)
        await model.loadMore() // nothing more: no request
        #expect(server.queries.count == 3)

        model.toggleFilter("4")
        await model.reload()
        #expect(model.items.count == 2)
        #expect(server.queries.last?.contains("store=4") == true)
    }

    @Test func aSelectedStoreKeepsItsChip() {
        let model = PurchasedModel(client: APIClient(baseURL: URL(string: "http://x")!))
        model.toggleFilter("9")
        let chips = model.chips(stores: [ShoppingStore(id: 9, name: "Costco"), ShoppingStore(id: 8, name: "Aldi")])
        #expect(chips.map(\.title) == ["Costco"])
    }
}
