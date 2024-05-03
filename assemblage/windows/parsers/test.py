import unittest
from proj import Project
import uuid


class TestStringMethods(unittest.TestCase):

    def test_change_version(self):
        version = str(uuid.uuid4())
        proj = Project("p.vcxproj")
        proj.set_toolset_version(version)
        proj.write()
        proj = Project("p.vcxproj")
        self.assertEqual(proj.get_toolset_version(), version)

    # def test_change_optimization(self):
    #     proj = Project("p.vcxproj")
    #     proj.set_optimization("Ox")
    #     proj.write()
    #     proj = Project("p.vcxproj")
    #     self.assertEqual(proj.get_optimization(), "Full")

    # def test_set_whole_program_optimization(self):
    #     proj = Project("p.vcxproj")
    #     proj.set_whole_program_optimization(False)
    #     proj.write()
    #     proj = Project("p.vcxproj")
    #     self.assertEqual(proj.get_whole_program_optimization(), "false")

    # def test_change_optimization_not_standard_XML(self):
    #     proj = Project("non_standard.vcxproj")
    #     proj.set_optimization("Ox")
    #     proj.write()
    #     proj = Project("non_standard.vcxproj")
    #     self.assertEqual(proj.get_optimization(), "Full")

    # def test_set_favorsizeorspeed(self):
    #     proj = Project("p.vcxproj")
    #     proj.set_favorsizeorspeed("Ot")
    #     proj.write()
    #     proj = Project("p.vcxproj")
    #     self.assertEqual(proj.get_favorsizeorspeed(), "Speed")

    # def test_set_inlinefunctionexpansion(self):
    #     proj = Project("p.vcxproj")
    #     proj.set_inlinefunctionexpansion("Ob1")
    #     proj.write()
    #     proj = Project("p.vcxproj")
    #     self.assertEqual(proj.get_inlinefunctionexpansion(), "OnlyExplicitInline")

    # def test_set_intrinsicfunctions(self):
    #     proj = Project("p.vcxproj")
    #     proj.disable_intrinsicfunctions()
    #     proj.write()
    #     proj = Project("p.vcxproj")
    #     self.assertEqual(proj.get_intrinsicfunctions(), "false")


if __name__ == '__main__':
    unittest.main()
