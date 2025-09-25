import { Button, MenuItem, Typography } from "@mui/material";
import Link from "next/link";

export type LinkMenuItemProps = {
  path: string;
  name: string;
  handleClose: () => void;
};

export default function LinkMenuItem({ path, name, handleClose }: LinkMenuItemProps) {
  return (
    <Link href={path} passHref style={{ textDecoration: 'none' }}> 
      <MenuItem onClick={handleClose}>
        <Button fullWidth variant="contained" sx={{
            '&:hover': {
                backgroundColor: 'secondary.main'
            }
        }}>
          <Typography
            variant="h6"
            sx={{
              textDecoration: 'none', 
              '&:hover': {
                textDecoration: 'underline',
              },
              '& a:visited': {
                textDecoration: 'none', 
              }
            }}
          >
            {name}
          </Typography>
        </Button>
      </MenuItem>
    </Link>
  );
}
