/* Acumyn's Privacy Policy and Terms of Service, rendered by pages/Legal.jsx at /privacy, /terms.
 *
 * WRITTEN FROM THE CODE, NOT FROM A TEMPLATE. Every statement about what Acumyn stores, sends or
 * deletes was checked against backend/app when this was written (September 2026): the
 * providers, the 365-day transcript purge, the read-only integrations, the absence of cookies
 * and analytics. When the product changes one of those, this file has to change with it — a
 * policy that describes last year's data flows is a policy that is wrong.
 *
 * WHAT THE CODE CANNOT SAY lives in LEGAL_FACTS. Until each one has a value the page shows a
 * "Draft" notice and a highlighted blank where the fact goes, so it cannot go live looking
 * finished. These are the company's to supply, and the finished text deserves a lawyer's read:
 * it makes commitments (deletion on request, notice of changes) as well as descriptions.
 *
 * A paragraph is a string, or an array of strings and {fact: "name"} pieces.
 */

export const LEGAL_FACTS = {
  entity: { value: "", ask: "legal name of the company that operates Acumyn" },
  contactEmail: { value: "", ask: "an address that receives mail" },
  governingLaw: { value: "", ask: "governing-law state" },
  effectiveDate: { value: "", ask: "effective date" },
};

export const LEGAL_IS_DRAFT = Object.values(LEGAL_FACTS).some((f) => !f.value);

const ENTITY = { fact: "entity" };
const CONTACT = { fact: "contactEmail" };
const LAW = { fact: "governingLaw" };

const privacy = {
  title: "Privacy Policy",
  intro: [
    ENTITY, " (“Acumyn”, “we”) makes Acumyn, software that real estate teams use to bring their production, books and operations into one place. This policy explains what information we handle, what we use it for, which other companies receive it, and how long we keep it. It covers acumyn.io, app.acumyn.io and every workspace we host, such as your-team.acumyn.io.",
  ],
  sections: [
    {
      id: "roles",
      h: "Whose data it is",
      blocks: [
        "A team that uses Acumyn — our customer — decides what goes into its workspace and who can see it. We handle that data on the team’s behalf and to provide the service to it. If you are a member of a workspace, the team that invited you is the first place to take questions about its data.",
        "We also handle a smaller amount of information for ourselves: account and sign-in details, security records, and messages you send us.",
      ],
    },
    {
      id: "information",
      h: "What we handle",
      blocks: [
        { list: [
          "Account details: your name, work email address, role and which parts of the workspace you can open. Passwords are stored only as bcrypt hashes. If you set up an authenticator app, its secret is stored encrypted and its recovery codes are stored as hashes.",
          "Google sign-in, if you use it: your email address, name and whether Google has verified the address. We use them only to match an account your team already created, and we do not keep Google’s tokens.",
          "Data from systems a workspace connects. Depending on which ones it uses — QuickBooks Online, Sisu, Follow Up Boss, GoHighLevel, Stripe, Arive and Meta Ads — this can include financial statements and transactions with their payees and memos, real estate transactions and the contact details of the clients on them, leads, members and payments, loan records including borrower contact details, and advertising results.",
          "Content a team adds: uploaded documents and images, training material, directory details such as a title, phone number or photo, marketing requests, and questions put to the in-product assistants.",
          "Call recordings and transcripts, only in a workspace that turns call recording on.",
          "Security records: an audit log of changes to people, access and connections, which can include the email address typed into a failed sign-in. To limit repeated sign-in and lookup attempts we count requests per IP address in memory for a few minutes; we do not store IP addresses in our database.",
        ] },
      ],
    },
    {
      id: "use",
      h: "What we use it for",
      blocks: [
        { list: [
          "To run the service: syncing and reconciling a team’s data, showing it to the people the team gives access, and sending the email the product sends — invitations, password resets, workspace links, deadline reminders and request notifications.",
          "To keep accounts and data secure: authentication, rate limits, two-factor checks and the audit log.",
          "To answer questions asked of the built-in assistants and to read documents uploaded for that purpose, using Anthropic’s models.",
        ] },
        "We do not sell personal information. We do not use a team’s data for advertising, and we do not use it to train AI models. Connections to other systems are read-only: Acumyn reads from them and does not change anything in them.",
      ],
    },
    {
      id: "providers",
      h: "Companies that receive data",
      blocks: [
        "We use these providers to run Acumyn. Each receives only what its job needs.",
        { table: {
          head: ["Provider", "What it receives", "Why"],
          rows: [
            ["Railway", "Everything the service stores: the application runs on Railway and its database is hosted there.", "Hosting"],
            ["Cloudflare R2", "Uploaded files: Binder documents, logos and images, training files, request attachments and headshots.", "File storage"],
            ["Anthropic", "The material an AI feature works on — for example an assistant question with the workspace data the asker is allowed to see, a whole uploaded document for Binder to read, or call transcript lines to divide into chapters.", "AI features"],
            ["Recall.ai", "The call itself: its bot joins through the meeting link as a visible participant, then records and transcribes the call. Recall keeps the recording under its own retention policy.", "Call recording, only where a workspace turns it on"],
            ["Resend", "The recipient’s address and the contents of each email the product sends.", "Email delivery"],
            ["Google", "Your sign-in, if you choose Google. Pages also load fonts from Google, which receives your IP address when they do.", "Sign-in and web fonts"],
          ],
        } },
        "A team can also send data to services it chooses: a marketing request posted to its own Slack workspace or webhook, or a training video embedded from YouTube, Vimeo or Loom. Those services handle it under their own terms.",
        "Our providers may process data in the United States and in other countries where they operate. We may also disclose information when the law requires it.",
      ],
    },
    {
      id: "storage",
      h: "Cookies and browser storage",
      blocks: [
        "Acumyn sets no cookies and runs no analytics or advertising trackers. It uses your browser’s own storage to keep you signed in — until the tab closes, or for up to 30 days — to hold a short-lived second-factor check, and to remember a workspace’s branding between visits.",
      ],
    },
    {
      id: "retention",
      h: "How long we keep it",
      blocks: [
        { list: [
          "A workspace’s data is kept while the workspace is active.",
          "Call transcripts are deleted automatically 365 days after they are stored. Recordings stay with Recall.ai under its retention; we do not copy them.",
          "Invitation links expire after 7 days and password reset links after 24 hours.",
          "Records read from GoHighLevel, Arive and Stripe are replaced on every sync, so a record deleted there drops out of Acumyn. Records from the other systems stay until they are deleted here.",
          "Deleting a Binder document deletes the stored file with it.",
        ] },
        ["When a team stops using Acumyn, its owner can ask us to delete the workspace’s data by writing to ", CONTACT, "."],
      ],
    },
    {
      id: "security",
      h: "Security",
      blocks: [
        "Credentials for connected systems and authenticator secrets are encrypted before they are stored, and a deployed server refuses to start with a default key. Connections to Acumyn use HTTPS. Every query is scoped to its own workspace, sensitive areas ask for a second factor again, and sign-in attempts are rate-limited and locked after repeated failures. No system is perfectly secure, and we will tell affected customers without undue delay if their data is compromised.",
      ],
    },
    {
      id: "rights",
      h: "Your choices",
      blocks: [
        ["Depending on where you live, you may have the right to see, correct or delete personal information about you, or to object to how it is used. If the information is in a team’s workspace, send the request to that team: it controls the data, and we will help it respond. For anything else, write to us at ", CONTACT, "."],
        "Acumyn is built for businesses and is not directed to children.",
      ],
    },
    {
      id: "changes",
      h: "Changes to this policy",
      blocks: [
        "When this policy changes we will update the effective date above, and we will email workspace owners before a change that affects how their data is used.",
      ],
    },
    {
      id: "contact",
      h: "Contact",
      blocks: [
        [ENTITY, " — ", CONTACT],
      ],
    },
  ],
};

const terms = {
  title: "Terms of Service",
  intro: [
    "These terms are an agreement between ", ENTITY, " (“Acumyn”, “we”) and the organization that uses Acumyn (the “customer”), and they also apply to each person the customer invites. If you accept them for an organization, you confirm that you are authorized to. Where the customer has a signed agreement with us, that agreement governs wherever it differs from these terms.",
  ],
  sections: [
    {
      id: "service",
      h: "The service",
      blocks: [
        "Acumyn gives each customer its own workspace. People join a workspace by invitation from the customer. We improve the service continually, so features change; features labelled early access are provided as they are and may be changed or withdrawn.",
      ],
    },
    {
      id: "accounts",
      h: "Accounts and access",
      blocks: [
        "The customer decides who is invited and what each person can see, and is responsible for that. Keep your sign-in details to yourself, and tell the customer or us promptly if you think someone else has used your account.",
      ],
    },
    {
      id: "connections",
      h: "Connected systems",
      blocks: [
        "When the customer connects a system such as QuickBooks Online, Sisu or Follow Up Boss, it authorizes Acumyn to read data from that system to provide the service. The customer must have the right to connect each system and to share its data with us. Connections are read-only. Each connected system is governed by its own terms, and we cannot control its availability or the data it returns.",
      ],
    },
    {
      id: "data",
      h: "Customer data",
      blocks: [
        "The customer owns its data. It grants us the rights we need to host, process, transmit and display that data to provide and support the service. Our Privacy Policy describes how we handle it.",
      ],
    },
    {
      id: "ai",
      h: "AI features",
      blocks: [
        "Some features use AI to answer questions, read documents or draft work. AI output can be wrong. Check it before relying on it; work the product drafts waits for a person to approve it.",
      ],
    },
    {
      id: "recording",
      h: "Call recording",
      blocks: [
        "If the customer turns on call recording, a recording bot joins calls as a visible participant. The customer is responsible for giving any notice and obtaining any consent that the law requires before a call is recorded.",
      ],
    },
    {
      id: "use",
      h: "Acceptable use",
      blocks: [
        { list: [
          "Do not use Acumyn to break the law or to infringe anyone’s rights.",
          "Do not upload or connect data you are not entitled to share.",
          "Do not try to reach another customer’s workspace, probe or overload the service, or get around its security or access controls.",
          "Do not resell or provide the service to others without our written agreement.",
        ] },
      ],
    },
    {
      id: "fees",
      h: "Fees",
      blocks: [
        "Fees, if any, are those set out in the customer’s order or plan.",
      ],
    },
    {
      id: "termination",
      h: "Suspension and ending",
      blocks: [
        "We may suspend access to protect the service, its users or their data, or when these terms are broken, and we will tell the customer when we do unless that would make things worse. The customer may stop using Acumyn at any time. When the service ends, access ends; a customer’s data is deleted on request as our Privacy Policy describes.",
      ],
    },
    {
      id: "disclaimers",
      h: "Disclaimers",
      blocks: [
        "Acumyn is provided “as is”. Its figures are only as accurate as the systems they come from. It is not financial, tax, legal or compliance advice: compliance deadlines and rules in Binder are aids to your own review, not a substitute for it. To the extent the law allows, we disclaim all warranties not stated in these terms.",
      ],
    },
    {
      id: "liability",
      h: "Limitation of liability",
      blocks: [
        "To the extent the law allows, neither party is liable for indirect, incidental, special or consequential damages, or for lost profits or lost data, and our total liability arising from the service is limited to the fees the customer paid us in the 12 months before the claim.",
      ],
    },
    {
      id: "changes",
      h: "Changes to these terms",
      blocks: [
        "We may update these terms. We will change the effective date above and email workspace owners before a material change takes effect. Continuing to use Acumyn after that means accepting the updated terms.",
      ],
    },
    {
      id: "law",
      h: "Governing law",
      blocks: [
        ["These terms are governed by the laws of ", LAW, ", and disputes about them will be resolved in the courts located there."],
      ],
    },
    {
      id: "contact",
      h: "Contact",
      blocks: [
        [ENTITY, " — ", CONTACT],
      ],
    },
  ],
};

export const DOCS = { privacy, terms };
