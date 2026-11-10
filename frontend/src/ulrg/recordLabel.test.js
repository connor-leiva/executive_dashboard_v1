import assert from "node:assert/strict";
import test from "node:test";
import { drawerSummary, recordLabel, titleCase } from "./scorecardMath.js";

test("lowercase person names become proper names", () => {
  assert.equal(recordLabel("adam speth"), "Adam Speth");
  assert.equal(recordLabel("frances mouser moon"), "Frances Mouser Moon");
  assert.equal(recordLabel("mcdonald"), "McDonald");
  assert.equal(recordLabel("macarthur"), "MacArthur");
  assert.equal(recordLabel("machine"), "Machine");
  assert.equal(recordLabel("mackenzie"), "Mackenzie");
  assert.equal(recordLabel("o'brien"), "O'Brien");
  assert.equal(recordLabel("d'angelo"), "D'Angelo");
  assert.equal(recordLabel("mary-jane watson"), "Mary-Jane Watson");
});

test("mixed case, emails, urls and ids stay as stored", () => {
  assert.equal(recordLabel("McDonald"), "McDonald");
  assert.equal(recordLabel("beCollective"), "beCollective");
  assert.equal(recordLabel("Frances Mouser Moon"), "Frances Mouser Moon");
  assert.equal(recordLabel("ada@springb.com"), "ada@springb.com");
  assert.equal(recordLabel("https://forummastermind.com/vip"), "https://forummastermind.com/vip");
  assert.equal(recordLabel("a1b2c3d4-e5f6-7890-abcd-ef1234567890"), "a1b2c3d4-e5f6-7890-abcd-ef1234567890");
  assert.equal(recordLabel("opp1:vip_guest_seat"), "opp1:vip_guest_seat");
});

test("subtitles, stages and the members summary are headings", () => {
  assert.equal(recordLabel("GHL · Active member"), "GHL · Active Member");
  assert.equal(recordLabel("VIP Guest: Confirmed · VIP Page · proxy"), "VIP Guest: Confirmed · VIP Page · Proxy");
  assert.equal(recordLabel("spring break vip"), "Spring Break VIP");
  assert.equal(drawerSummary("57 active members."), "57 Active Members");
  assert.equal(drawerSummary("3 seats entered this week."), "3 Seats Entered This Week");
  assert.equal(
    drawerSummary("GHL hasn't synced this week, so these leads can't be listed."),
    "GHL hasn't synced this week, so these leads can't be listed.",
  );
  assert.equal(titleCase("57 records · from GHL"), "57 Records · From GHL");
});
