import { type Lesson, validateLesson } from "./whiteboard-tutor";

/** Authored practice examples. These do not read coursework or call a model. */
export const whiteboardLessons: readonly Lesson[] = [
  {
    version: 1,
    id: "ece-flip-flop",
    title: "One bit, one clock edge",
    subject: "Electrical engineering",
    topic: "D flip-flops",
    minutes: 4,
    description:
      "A conceptual positive-edge-triggered D flip-flop, with stable inputs and no asynchronous reset.",
    steps: [
      {
        id: "ece-setup",
        title: "A tiny memory",
        say: "A D flip-flop stores one bit. [[ece-memory]] Its D input supplies the next bit. [[ece-input]] The input feeds the memory. [[ece-wire]]",
        actions: [
          {
            id: "ece-memory",
            type: "shape",
            shape: "box",
            label: "D flip-flop: stores Q",
            place: "top-left",
          },
          {
            id: "ece-input",
            type: "shape",
            shape: "ellipse",
            label: "D: next input bit",
            place: "right-of:ece-memory",
          },
          {
            id: "ece-wire",
            type: "arrow",
            from: "ece-input",
            to: "ece-memory",
            label: "data input",
          },
        ],
      },
      {
        id: "ece-edge",
        title: "Wait for the rising edge",
        say: "At a rising clock edge, Q takes the value of D. [[ece-rule]] Between edges, it keeps its previous value. [[ece-hold]] This is the edge-triggered part. [[ece-emphasis]] Assume D is stable around the clock edge; real devices also have setup and hold requirements.",
        actions: [
          {
            id: "ece-rule",
            type: "math",
            latex: "Q_{\\text{after rising edge}} = D_{\\text{at edge}}",
            place: "next-row",
          },
          {
            id: "ece-hold",
            type: "write",
            text: "D changes between edges? Q waits. At the next rising edge, Q samples D.",
            place: "below:ece-rule",
          },
          {
            id: "ece-emphasis",
            type: "highlight",
            target: "ece-rule",
            style: "underline",
          },
        ],
        check: {
          question: "D becomes 1 between clock edges. When can Q take that 1?",
          choices: [
            "Immediately when D changes",
            "At the next rising edge, if D remains stable",
            "Only when the power is removed",
          ],
          answer: 1,
          explanation:
            "This positive-edge-triggered flip-flop samples D on the rising edge. Between edges, it holds Q.",
        },
      },
      {
        id: "ece-example",
        title: "Try a clock edge",
        say: "Suppose Q is zero and D is one just before the next valid rising edge. [[ece-example-line]] After that edge, Q is one. A latch behaves differently: while enabled, it can follow D.",
        actions: [
          {
            id: "ece-example-line",
            type: "write",
            text: "Before edge: D = 1, Q = 0. After edge: Q = 1.",
            place: "next-row",
          },
        ],
        check: {
          question:
            "If D is 0 at the next valid rising edge, what does Q become?",
          choices: ["1", "0", "It must keep its old value"],
          answer: 1,
          explanation:
            "At a valid rising edge, Q copies D. Here D is 0, so Q becomes 0.",
        },
      },
    ],
  },
  {
    version: 1,
    id: "calculus-parts",
    title: "Integration by parts, one choice at a time",
    subject: "Calculus",
    topic: "Integration by parts",
    minutes: 5,
    description:
      "A worked practice integral, with a choice to try before the next step is revealed.",
    steps: [
      {
        id: "parts-pick",
        title: "Choose what gets simpler",
        say: "Our practice integral is x times e to the x. [[parts-problem]] Choose u so that differentiating it makes it simpler.",
        actions: [
          {
            id: "parts-problem",
            type: "math",
            latex: "\\int x e^x\\,dx",
            place: "top-left",
          },
        ],
        check: {
          question: "Which choice of u becomes simpler when differentiated?",
          choices: ["u = x", "u = e^x"],
          answer: 0,
          explanation:
            "Differentiating x gives 1, so the remaining integral becomes easier. This is a useful choice, not a rule that other choices are forbidden.",
        },
      },
      {
        id: "parts-assign",
        title: "Match the pieces",
        say: "Set u equal to x and dv equal to e to the x dx. [[parts-values]] Integration by parts comes from the product rule. [[parts-identity]]",
        actions: [
          {
            id: "parts-values",
            type: "math",
            latex: "u=x,\\quad du=dx,\\quad dv=e^x dx,\\quad v=e^x",
            place: "next-row",
          },
          {
            id: "parts-identity",
            type: "math",
            latex: "\\int u\\,dv = uv - \\int v\\,du",
            place: "below:parts-values",
          },
        ],
        check: {
          question: "After substitution, which integral is left to subtract?",
          choices: [
            "The integral of x squared",
            "The integral of e^x",
            "The integral of 1/x",
          ],
          answer: 1,
          explanation:
            "The remaining term is the integral of v du. With v = e^x and du = dx, that is the integral of e^x.",
        },
      },
      {
        id: "parts-finish",
        title: "Finish, then differentiate to check",
        say: "Substitute, then integrate the remaining exponential. [[parts-answer]] Keep the minus sign and the integration constant. [[parts-sign]] Differentiate the result: the exponential terms cancel, leaving the original integrand. [[parts-proof]]",
        actions: [
          {
            id: "parts-answer",
            type: "math",
            latex: "\\int xe^x dx = xe^x-e^x+C",
            place: "next-row",
          },
          {
            id: "parts-sign",
            type: "highlight",
            target: "parts-answer",
            style: "circle",
          },
          {
            id: "parts-proof",
            type: "math",
            latex: "\\frac{d}{dx}(xe^x-e^x+C)=e^x+xe^x-e^x=xe^x",
            place: "below:parts-answer",
          },
        ],
      },
    ],
  },
  {
    version: 1,
    id: "physics-net-force",
    title: "Net force tells you the acceleration",
    subject: "Physics",
    topic: "Newton's second law",
    minutes: 4,
    description:
      "A one-dimensional practice example with constant mass, SI units and a net force.",
    steps: [
      {
        id: "force-setup",
        title: "Use the net force",
        say: "Picture a three kilogram object. [[force-object]] The total horizontal force is twelve newtons to the right. [[force-given]] Net force means all the forces have already been combined.",
        actions: [
          {
            id: "force-object",
            type: "shape",
            shape: "box",
            label: "Object: m = 3 kg",
            place: "top-left",
          },
          {
            id: "force-given",
            type: "write",
            text: "Net horizontal force: +12 N. Take right as positive.",
            place: "right-of:force-object",
          },
        ],
        check: {
          question: "Which force belongs in F = ma?",
          choices: [
            "The largest individual force",
            "The net force, the sum of all forces",
            "Only friction",
          ],
          answer: 1,
          explanation:
            "Newton's second law relates the vector sum of forces to mass times acceleration.",
        },
      },
      {
        id: "force-calculate",
        title: "Divide by mass",
        say: "For constant mass in an inertial frame, net force equals mass times acceleration. [[force-equation]] Divide by three kilograms. [[force-answer]] The acceleration is positive four metres per second squared, so it points right.",
        actions: [
          {
            id: "force-equation",
            type: "math",
            latex: "F_{\\text{net}}=ma",
            place: "next-row",
          },
          {
            id: "force-answer",
            type: "math",
            latex:
              "a=\\frac{12\\,\\mathrm{N}}{3\\,\\mathrm{kg}}=4\\,\\mathrm{m/s^2}",
            place: "below:force-equation",
          },
        ],
        check: {
          question:
            "If mass doubles while the same net force acts, what happens to acceleration?",
          choices: ["It doubles", "It stays the same", "It halves"],
          answer: 2,
          explanation:
            "Acceleration is net force divided by mass. Doubling only the mass halves the acceleration.",
        },
      },
      {
        id: "force-graph",
        title: "See the proportional relationship",
        say: "For a separate one kilogram example, plotting force vertically against acceleration horizontally gives a straight line. [[force-plot]] Here each extra metre per second squared requires one extra newton. The three kilogram object's line would be three times as steep.",
        actions: [
          {
            id: "force-plot",
            type: "plot",
            kind: "linear",
            domain: [0, 5],
            range: [0, 5],
            label: "F = a for m = 1 kg; x: a, y: F",
            place: "next-row",
          },
        ],
      },
    ],
  },
  {
    version: 1,
    id: "linear-algebra-dot-product",
    title: "A dot product measures alignment",
    subject: "Linear algebra",
    topic: "Dot products",
    minutes: 4,
    description:
      "A two-dimensional practice example connecting component arithmetic to perpendicular vectors.",
    steps: [
      {
        id: "dot-vectors",
        title: "Multiply matching components",
        say: "Take vectors two comma one and three comma four. [[dot-inputs]] Multiply the first components together, then the second components together, and add. [[dot-rule]]",
        actions: [
          {
            id: "dot-inputs",
            type: "math",
            latex: "\\mathbf{u}=(2,1),\\qquad\\mathbf{v}=(3,4)",
            place: "top-left",
          },
          {
            id: "dot-rule",
            type: "math",
            latex: "\\mathbf{u}\\cdot\\mathbf{v}=u_1v_1+u_2v_2",
            place: "below:dot-inputs",
          },
        ],
        check: {
          question: "What is (2, 1) dot (3, 4)?",
          choices: ["10", "11", "(6, 4)"],
          answer: 0,
          explanation:
            "Two times three plus one times four is 6 + 4 = 10. The result is a scalar, not a vector.",
        },
      },
      {
        id: "dot-result",
        title: "A scalar with geometric meaning",
        say: "The component calculation gives ten. [[dot-answer]] Geometrically, the dot product also equals the product of the lengths times the cosine of their angle. [[dot-geometry]]",
        actions: [
          {
            id: "dot-answer",
            type: "math",
            latex: "(2)(3)+(1)(4)=10",
            place: "next-row",
          },
          {
            id: "dot-geometry",
            type: "math",
            latex:
              "\\mathbf{u}\\cdot\\mathbf{v}=\\lVert\\mathbf{u}\\rVert\\,\\lVert\\mathbf{v}\\rVert\\cos\\theta",
            place: "below:dot-answer",
          },
        ],
      },
      {
        id: "dot-perpendicular",
        title: "Recognize a right angle",
        say: "For nonzero perpendicular vectors, the angle is ninety degrees and its cosine is zero. [[dot-zero]] The zero vector also has zero dot product with every vector, but it has no defined direction.",
        actions: [
          {
            id: "dot-zero",
            type: "write",
            text: "Nonzero u and v: u dot v = 0 means they are perpendicular.",
            place: "next-row",
          },
        ],
        check: {
          question: "Are (1, 0) and (0, 5) perpendicular?",
          choices: [
            "Yes: their dot product is 0",
            "No: their lengths differ",
            "Only if both have length 1",
          ],
          answer: 0,
          explanation:
            "Their dot product is 1 times 0 plus 0 times 5, which is 0. Both are nonzero, so they are perpendicular.",
        },
      },
    ],
  },
  {
    version: 1,
    id: "statistics-mean-variance",
    title: "Mean is center; variance is spread",
    subject: "Statistics",
    topic: "Population mean and variance",
    minutes: 5,
    description:
      "Treat 4, 6 and 8 as the entire population. Distinguish population variance from sample variance.",
    steps: [
      {
        id: "stats-center",
        title: "Find the center",
        say: "Our entire population has three values: four, six and eight. [[stats-data]] Add them and divide by three to get a mean of six. [[stats-mean]]",
        actions: [
          {
            id: "stats-data",
            type: "write",
            text: "Entire population: 4, 6, 8. Population size N = 3.",
            place: "top-left",
          },
          {
            id: "stats-mean",
            type: "math",
            latex: "\\mu=\\frac{4+6+8}{3}=6",
            place: "below:stats-data",
          },
        ],
        check: {
          question: "What do the deviations from the mean add up to?",
          choices: ["6", "0", "8"],
          answer: 1,
          explanation:
            "The deviations are -2, 0 and 2. They add to 0, so averaging raw deviations would hide the spread.",
        },
      },
      {
        id: "stats-spread",
        title: "Square before averaging",
        say: "Square each distance from the mean, then average those squares. [[stats-variance]] Since these three values are the entire population, divide by N, which is three. [[stats-denominator]]",
        actions: [
          {
            id: "stats-variance",
            type: "math",
            latex: "\\sigma^2=\\frac{(4-6)^2+(6-6)^2+(8-6)^2}{3}=\\frac{8}{3}",
            place: "next-row",
          },
          {
            id: "stats-denominator",
            type: "highlight",
            target: "stats-variance",
            style: "wash",
          },
        ],
        check: {
          question: "What is the population variance of 4, 6 and 8?",
          choices: ["0", "4", "8/3"],
          answer: 2,
          explanation:
            "The squared deviations are 4, 0 and 4. Their average is 8/3. Dividing by N - 1 would instead give the usual sample variance estimate, 4.",
        },
      },
      {
        id: "stats-units",
        title: "Return to the original units",
        say: "Variance has squared units. Standard deviation is its square root. [[stats-sd]] Here the population standard deviation is about one point six three in the original units.",
        actions: [
          {
            id: "stats-sd",
            type: "math",
            latex: "\\sigma=\\sqrt{\\frac{8}{3}}\\approx1.63",
            place: "next-row",
          },
        ],
      },
    ],
  },
].map(validateLesson);
